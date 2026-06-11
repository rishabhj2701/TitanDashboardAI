import { useState, useRef, useCallback, useEffect } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import Papa from 'papaparse';
import * as XLSX from 'xlsx';
import { uploadDatasetFile, uploadDatasetUrl, getDatasetById } from '../api/ingestionClient';
import { ApiError } from '../api/types';
import type { ChatMessage, UploadedFileData } from '../features/chat/types';
import { asObject, detectDatasetType, getCodeMappingReminder } from '../features/chat/mappers';

type GenericObject = Record<string, unknown>;

const normalizeStringArray = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value.map((v) => String(v));
};

const normalizeDatasetType = (value: unknown): string => {
  const s = typeof value === 'string' ? value : String(value ?? '');
  return s.trim() || 'unknown';
};

const normalizeQueryableFields = (value: unknown) => {
  const obj = asObject(value);
  const fields = Array.isArray(obj?.fields) ? obj.fields : [];
  return fields
    .map((field) => {
      const item = asObject(field);
      const queryName = String(item?.query_name ?? '').trim();
      const sourceColumn = String(item?.source_column ?? '').trim();
      if (!queryName || !sourceColumn) return null;
      return {
        queryName,
        sourceColumn,
        enabled: Boolean(item?.enabled ?? true),
        locked: Boolean(item?.locked ?? false),
      };
    })
    .filter((item): item is { queryName: string; sourceColumn: string; enabled: boolean; locked: boolean } => Boolean(item));
};


type UseFileUploadParams = {
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setIsLoading: Dispatch<SetStateAction<boolean>>;
};

type UseFileUploadResult = {
  uploadedFileData: UploadedFileData | null;
  setUploadedFileData: Dispatch<SetStateAction<UploadedFileData | null>>;
  showUploadMenu: boolean;
  setShowUploadMenu: Dispatch<SetStateAction<boolean>>;
  uploadMode: 'file' | 'url';
  setUploadMode: Dispatch<SetStateAction<'file' | 'url'>>;
  uploadUrl: string;
  setUploadUrl: Dispatch<SetStateAction<string>>;
  uploadStatus: string | null;
  setUploadStatus: Dispatch<SetStateAction<string | null>>;
  uploadError: string | null;
  setUploadError: Dispatch<SetStateAction<string | null>>;
  uploadInProgress: boolean;
  isDragging: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  handleFileSelection: (file: File) => void;
  handleMultipleFiles: (files: File[]) => void;
  handleUrlUpload: () => Promise<void>;
  resetUploadState: () => void;
};

const uploadFileToBackend = async (file: File): Promise<GenericObject | null> => {
  try {
    return asObject(await uploadDatasetFile(file));
  } catch (error) {
    console.error('Failed to upload file to backend:', error);
    if (error instanceof ApiError) {
      return { error: error.message || `Upload failed (${error.status}).`, status: error.status };
    }
    return { error: 'Upload failed. Check network/proxy/backend status.' };
  }
};

const uploadUrlToBackend = async (url: string): Promise<GenericObject | null> => {
  try {
    return asObject(await uploadDatasetUrl({ url }));
  } catch (error) {
    console.error('Failed to upload URL to backend:', error);
    if (error instanceof ApiError) {
      return { error: error.message || `URL upload failed (${error.status})`, status: error.status };
    }
    return { error: 'URL upload failed. Check the link and try again.' };
  }
};

const isCodebookResponse = (resp: unknown): boolean => {
  const value = asObject(resp);
  return Boolean(value && !value.dataset_id && value.codebook);
};

const hydrateUploadedDataset = async (datasetId: string): Promise<GenericObject | null> => {
  try {
    return asObject(await getDatasetById(datasetId));
  } catch {
    return null;
  }
};

export const useFileUpload = ({ setMessages, setIsLoading }: UseFileUploadParams): UseFileUploadResult => {
  const [, setUploadedFile] = useState<File | null>(null);
  const [uploadedFileData, setUploadedFileData] = useState<UploadedFileData | null>(null);
  const [showUploadMenu, setShowUploadMenu] = useState(false);
  const [uploadMode, setUploadMode] = useState<'file' | 'url'>('file');
  const [uploadUrl, setUploadUrl] = useState('');
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadInProgress, setUploadInProgress] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);

  const handleAgentHandoff = useCallback(async (
    file: File,
    fileData: UploadedFileData,
    queuePrefix: string
  ) => {
    const uploadResponse = await uploadFileToBackend(file);

    if (uploadResponse?.error) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `${queuePrefix}${uploadResponse.error}`
      }]);
      return;
    }

    const resp = uploadResponse as GenericObject;
    if (isCodebookResponse(resp)) {
      const codebook = resp.codebook as GenericObject | undefined;
      const attrs = (codebook?.attributes as string | number | undefined) ?? (codebook?.inserted as string | number | undefined) ?? '—';
      const warning =
        typeof resp.warning === 'string' && resp.warning.trim()
          ? `\nNote: ${resp.warning.trim()}`
          : '';
      setMessages(prev => [...prev, {
        role: 'assistant',
        content:
          `${queuePrefix}${(resp.message as string) || 'Codebook uploaded successfully.'}\n` +
          `Attributes loaded: ${attrs}\n` +
          `You can now review and edit code mappings in the Ingestion tab. [[OPEN_INGESTION]]${warning}`
      }]);
      return;
    }

    if (resp.dataset_id) {
      setUploadedFileData(fileData);
      const datasetId = resp.dataset_id as string;
      const datasetName = (resp.name || resp.dataset || file.name) as string;
      const roadMatch = (resp.road_match as GenericObject) || {};
      const matchRateValue =
        typeof roadMatch.match_rate === 'number'
          ? roadMatch.match_rate
          : (roadMatch.matched && roadMatch.total ? (roadMatch.matched as number) / (roadMatch.total as number) : undefined);
      const matchRate = matchRateValue !== undefined
        ? `${(matchRateValue * 100).toFixed(1)}%`
        : '—';

      const detail = await hydrateUploadedDataset(datasetId);
      const entityType = (
        resp.entity_type ||
        detail?.entity_type ||
        fileData.datasetType ||
        ''
      ).toString().toLowerCase();
      const codebookInfo = (detail?.codebook as GenericObject) ?? (detail?.stats as GenericObject)?.codebook ?? (resp.codebook as GenericObject) ?? {};
      const codeReminder =
        (typeof resp.code_mapping_reminder === 'string' && resp.code_mapping_reminder.trim())
          ? resp.code_mapping_reminder.trim()
          : getCodeMappingReminder(entityType, codebookInfo);

      if (detail) {
        const queryableFields = normalizeQueryableFields(detail.queryable_fields);
        setUploadedFileData(prev => prev ? {
          ...prev,
          datasetName,
          datasetId,
          datasetType: entityType || prev.datasetType,
          rowCount: (detail.row_count as number) ?? prev.rowCount,
          columns: detail.columns ? normalizeStringArray(detail.columns) : prev.columns,
          data: (detail.preview_rows as unknown[]) ?? prev.data,
          queryableFields: queryableFields.length ? queryableFields : prev.queryableFields,
        } : prev);
      } else {
        setUploadedFileData(prev => prev ? { ...prev, datasetName, datasetId, datasetType: entityType || prev.datasetType } : prev);
      }

      const ingest = resp.ingest as GenericObject | undefined;
      setMessages(prev => [...prev, {
        role: 'assistant',
        content:
          `${queuePrefix}Dataset **${datasetName}** registered.\n` +
          `ID: ${datasetId}\n` +
          `Rows: ${(ingest?.rows_inserted as number) ?? (detail?.row_count as number) ?? (resp.rows as number) ?? fileData.rowCount} | Columns: ${(detail?.columns as unknown[])?.length ?? fileData.columns.length}\n` +
          `Road match: ${roadMatch.matched !== undefined ? matchRate : 'not applied'}\n` +
          `${codeReminder ? `${codeReminder}\n` : ''}` +
          `Please check the Ingestion tab up top to verify your mapping and sample rows. [[OPEN_INGESTION]]`
      }]);
    } else {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `${queuePrefix}Upload failed. Please try again or check the backend logs.`
      }]);
    }
  }, [setMessages]);

  const processUploadedFile = useCallback(async (
    file: File,
    queueMeta?: { index: number; total: number }
  ): Promise<void> => {
    const fileExtension = file.name.split('.').pop()?.toLowerCase();
    const queuePrefix = queueMeta ? `[${queueMeta.index}/${queueMeta.total}] ` : '';

    try {
      if (fileExtension === 'csv') {
        await new Promise<void>((resolve) => {
          Papa.parse(file, {
            header: true,
            complete: (results) => {
              (async () => {
                const data = results.data;
                const columns = results.meta.fields || [];
                const datasetType = detectDatasetType(data, columns, file.name);
                const fileData = {
                  fileName: file.name,
                  fileType: 'CSV',
                  rowCount: data.length,
                  columns,
                  data,
                  preview: '',
                  datasetType
                };
                await handleAgentHandoff(file, fileData, queuePrefix);
              })()
                .catch((error) => {
                  console.error('CSV parse error:', error);
                  setMessages(prev => [...prev, {
                    role: 'assistant',
                    content: `${queuePrefix}Error reading CSV file: ${error?.message ?? 'Unknown error'}`
                  }]);
                })
                .finally(() => resolve());
            },
            error: (error) => {
              console.error('CSV parse error:', error);
              setMessages(prev => [...prev, {
                role: 'assistant',
                content: `${queuePrefix}Error reading CSV file: ${error.message}`
              }]);
              resolve();
            }
          });
        });
      }
      else if (fileExtension === 'xlsx' || fileExtension === 'xls') {
        await new Promise<void>((resolve) => {
          const reader = new FileReader();
          reader.onload = (e) => {
            (async () => {
              const data = new Uint8Array(e.target?.result as ArrayBuffer);
              const workbook = XLSX.read(data, { type: 'array' });
              const firstSheet = workbook.Sheets[workbook.SheetNames[0]];
              const jsonData = XLSX.utils.sheet_to_json(firstSheet);
              const columns = Object.keys(jsonData[0] || {});
              const datasetType = detectDatasetType(jsonData, columns, file.name);
              const fileData = {
                fileName: file.name,
                fileType: 'Excel',
                rowCount: jsonData.length,
                columns,
                data: jsonData,
                preview: '',
                datasetType
              };
              await handleAgentHandoff(file, fileData, queuePrefix);
            })()
              .catch((error) => {
                console.error('Excel parse error:', error);
                setMessages(prev => [...prev, {
                  role: 'assistant',
                  content: `${queuePrefix}Error reading Excel file: ${error?.message ?? 'Unknown error'}`
                }]);
              })
              .finally(() => resolve());
          };
          reader.onerror = () => {
            setMessages(prev => [...prev, {
              role: 'assistant',
              content: `${queuePrefix}Error reading Excel file.`
            }]);
            resolve();
          };
          reader.readAsArrayBuffer(file);
        });
      }
      else if (fileExtension === 'json' || fileExtension === 'geojson') {
        await new Promise<void>((resolve) => {
          const reader = new FileReader();
          reader.onload = (e) => {
            (async () => {
              const jsonData = JSON.parse(e.target?.result as string);
              let dataArray: any[];
              let columns: string[];
              let datasetType: string;

              if (jsonData.type === 'FeatureCollection' && Array.isArray(jsonData.features)) {
                dataArray = [{ type: jsonData.type, features: jsonData.features }];
                columns = ['type', 'features'];
                datasetType = 'workzone';
                const fileData = {
                  fileName: file.name,
                  fileType: 'GeoJSON',
                  rowCount: jsonData.features.length,
                  columns,
                  data: dataArray,
                  preview: '',
                  datasetType
                };
                await handleAgentHandoff(file, fileData, queuePrefix);
              } else {
                dataArray = Array.isArray(jsonData) ? jsonData : [jsonData];
                columns = Object.keys(dataArray[0] || {});
                datasetType = detectDatasetType(dataArray, columns, file.name);
                const fileData = {
                  fileName: file.name,
                  fileType: 'JSON',
                  rowCount: dataArray.length,
                  columns,
                  data: dataArray,
                  preview: '',
                  datasetType
                };
                await handleAgentHandoff(file, fileData, queuePrefix);
              }
            })()
              .catch((error) => {
                console.error('JSON parse error:', error);
                setMessages(prev => [...prev, {
                  role: 'assistant',
                  content: `${queuePrefix}Error reading JSON file: ${error?.message ?? 'Unknown error'}`
                }]);
              })
              .finally(() => resolve());
          };
          reader.onerror = () => {
            setMessages(prev => [...prev, {
              role: 'assistant',
              content: `${queuePrefix}Error reading JSON file.`
            }]);
            resolve();
          };
          reader.readAsText(file);
        });
      }
      else if (fileExtension === 'txt') {
        await new Promise<void>((resolve) => {
          const reader = new FileReader();
          reader.onload = (e) => {
            (async () => {
              const textContent = e.target?.result as string;
              const lines = textContent.split('\n').filter(line => line.trim());
              const fileData = {
                fileName: file.name,
                fileType: 'Text',
                rowCount: lines.length,
                columns: [],
                data: lines.map((line, idx) => ({ line: idx + 1, content: line })),
                preview: '',
                datasetType: 'text'
              };
              await handleAgentHandoff(file, fileData, queuePrefix);
            })()
              .catch((error) => {
                console.error('Text parse error:', error);
                setMessages(prev => [...prev, {
                  role: 'assistant',
                  content: `${queuePrefix}Error reading text file: ${error?.message ?? 'Unknown error'}`
                }]);
              })
              .finally(() => resolve());
          };
          reader.onerror = () => {
            setMessages(prev => [...prev, {
              role: 'assistant',
              content: `${queuePrefix}Error reading text file.`
            }]);
            resolve();
          };
          reader.readAsText(file);
        });
      }
      else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `${queuePrefix}Unsupported file type: ${fileExtension}. Supported formats: CSV, Excel (.xlsx, .xls), JSON, GeoJSON, TXT`
        }]);
      }
    } catch (error: any) {
      console.error('File processing error:', error);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `${queuePrefix}Error processing file: ${error.message}`
      }]);
    }
  }, [handleAgentHandoff, setMessages]);

  const handleFileSelection = useCallback((file: File) => {
    setUploadedFile(file);
    setMessages(prev => [...prev, {
      role: 'user',
      content: `📎 Uploading: ${file.name}...`
    }]);
    setShowUploadMenu(false);
    setIsLoading(true);
    void processUploadedFile(file).finally(() => setIsLoading(false));
  }, [processUploadedFile, setMessages, setIsLoading]);

  const handleMultipleFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;

    const orderedNames = files.map((f) => f.name).join(', ');
    setMessages(prev => [...prev, {
      role: 'user',
      content: `📎 Received ${files.length} files. I'll process them one at a time in this order: ${orderedNames}`
    }]);
    setShowUploadMenu(false);
    setIsLoading(true);

    void (async () => {
      for (let i = 0; i < files.length; i += 1) {
        const file = files[i];
        setUploadedFile(file);
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `Starting ${i + 1}/${files.length}: **${file.name}**`
        }]);
        await processUploadedFile(file, { index: i + 1, total: files.length });
      }
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Finished processing ${files.length} file${files.length === 1 ? '' : 's'}.`
      }]);
    })()
      .catch((error: any) => {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `Upload queue failed: ${error?.message ?? 'Unknown error'}`
        }]);
      })
      .finally(() => setIsLoading(false));
  }, [processUploadedFile, setMessages, setIsLoading]);

  const handleUrlUpload = useCallback(async () => {
    if (!uploadUrl.trim() || uploadInProgress) return;
    setUploadError(null);
    setUploadStatus('Loading URL...');
    setUploadInProgress(true);

    const trimmed = uploadUrl.trim();
    setMessages(prev => [...prev, {
      role: 'user',
      content: `🔗 Loading: ${trimmed}`
    }]);

    const uploadResponse = await uploadUrlToBackend(trimmed);
    const resp = uploadResponse as GenericObject;
    if (isCodebookResponse(resp)) {
      const codebook = resp.codebook as GenericObject | undefined;
      const attrs = (codebook?.attributes as string | number | undefined) ?? (codebook?.inserted as string | number | undefined) ?? '—';
      setMessages(prev => [...prev, {
        role: 'assistant',
        content:
          `${(resp.message as string) || 'Codebook uploaded successfully.'}\n` +
          `Attributes loaded: ${attrs}\n` +
          `You can now review and edit code mappings in the Ingestion tab. [[OPEN_INGESTION]]`
      }]);
      setUploadStatus((resp.message as string) || 'Codebook uploaded');
      setUploadInProgress(false);
      setUploadUrl('');
      setShowUploadMenu(false);
      return;
    }

    if (!resp.dataset_id) {
      setUploadError((resp.error as string) || 'URL upload failed. Check the link and try again.');
      setUploadStatus(null);
      setUploadInProgress(false);
      return;
    }

    const datasetId = resp.dataset_id as string;
    const datasetName = (resp.name || resp.dataset || 'upload') as string;
    const roadMatch = (resp.road_match as GenericObject) || {};
    const matchRateValue =
      typeof roadMatch.match_rate === 'number'
        ? roadMatch.match_rate
        : (roadMatch.matched && roadMatch.total ? (roadMatch.matched as number) / (roadMatch.total as number) : undefined);
    const matchRate = matchRateValue !== undefined
      ? `${(matchRateValue * 100).toFixed(1)}%`
      : '—';

    const detail = await hydrateUploadedDataset(datasetId);
    const entityType = (
      resp.entity_type ||
      detail?.entity_type ||
      ''
    ).toString().toLowerCase();
    const codebookInfo = (detail?.codebook as GenericObject) ?? (detail?.stats as GenericObject)?.codebook ?? (resp.codebook as GenericObject) ?? {};
    const codeReminder =
      (typeof resp.code_mapping_reminder === 'string' && resp.code_mapping_reminder.trim())
        ? resp.code_mapping_reminder.trim()
        : getCodeMappingReminder(entityType, codebookInfo);

    if (detail) {
      const queryableFields = normalizeQueryableFields(detail.queryable_fields);
      const ingest = resp.ingest as GenericObject | undefined;
      setUploadedFileData({
      fileName: datasetName,
      fileType: 'URL',
      rowCount: (detail.row_count as number) ?? (ingest?.rows_inserted as number) ?? (resp.rows as number) ?? 0,
      columns: normalizeStringArray(detail.columns),
      data: (detail.preview_rows as unknown[]) ?? [],
      preview: '',
      datasetType: normalizeDatasetType(entityType),
      datasetName,
      datasetId,
      queryableFields,
    });
    }

    const ingest = resp.ingest as GenericObject | undefined;
    setMessages(prev => [...prev, {
      role: 'assistant',
      content:
        `Dataset **${datasetName}** registered from URL.\n` +
        `ID: ${datasetId}\n` +
        `Rows: ${(ingest?.rows_inserted as number) ?? (detail?.row_count as number) ?? '—'} | Columns: ${(detail?.columns as unknown[])?.length ?? '—'}\n` +
        `Road match: ${roadMatch.matched !== undefined ? matchRate : 'not applied'}\n` +
        `${codeReminder ? `${codeReminder}\n` : ''}` +
        `Please check the Ingestion tab up top to verify your mapping and sample rows. [[OPEN_INGESTION]]`
    }]);

    setUploadStatus(`Loaded ${datasetName}`);
    setUploadInProgress(false);
    setUploadUrl('');
    setShowUploadMenu(false);
  }, [uploadUrl, uploadInProgress, setMessages]);

  // Drag-and-drop
  useEffect(() => {
    const hasFiles = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types || []).includes('Files');

    const handleDragEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragCounterRef.current += 1;
      setIsDragging(true);
    };

    const handleDragOver = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
    };

    const handleDragLeave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      dragCounterRef.current -= 1;
      if (dragCounterRef.current <= 0) {
        setIsDragging(false);
      }
    };

    const handleDrop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragCounterRef.current = 0;
      setIsDragging(false);
      const files = event.dataTransfer?.files;
      if (files && files.length > 0) {
        const fileArray = Array.from(files);
        if (fileArray.length === 1) {
          handleFileSelection(fileArray[0]);
        } else {
          handleMultipleFiles(fileArray);
        }
      }
    };

    window.addEventListener('dragenter', handleDragEnter);
    window.addEventListener('dragover', handleDragOver);
    window.addEventListener('dragleave', handleDragLeave);
    window.addEventListener('drop', handleDrop);

    return () => {
      window.removeEventListener('dragenter', handleDragEnter);
      window.removeEventListener('dragover', handleDragOver);
      window.removeEventListener('dragleave', handleDragLeave);
      window.removeEventListener('drop', handleDrop);
    };
  }, [handleFileSelection, handleMultipleFiles]);

  const resetUploadState = useCallback(() => {
    setUploadedFileData(null);
    setUploadStatus(null);
    setUploadError(null);
    setUploadUrl('');
    setUploadInProgress(false);
    setShowUploadMenu(false);
  }, []);

  return {
    uploadedFileData,
    setUploadedFileData,
    showUploadMenu,
    setShowUploadMenu,
    uploadMode,
    setUploadMode,
    uploadUrl,
    setUploadUrl,
    uploadStatus,
    setUploadStatus,
    uploadError,
    setUploadError,
    uploadInProgress,
    isDragging,
    fileInputRef,
    handleFileSelection,
    handleMultipleFiles,
    handleUrlUpload,
    resetUploadState,
  };
};
