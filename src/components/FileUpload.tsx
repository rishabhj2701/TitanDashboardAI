import React, { useCallback, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import Papa from 'papaparse';
import * as XLSX from 'xlsx';
import JSZip from 'jszip';
import { uploadDatasetFile } from '../api/ingestionClient';
import { formatDateTimeHuman } from '../utils/dateTime';

// Inside FileUpload.tsx

interface UploadedFile {
  name: string;
  size: number;
  type: string;
  uploadTime: Date;
  data: any[];
  columns: string[];
  stats: ColumnStats[];
  file: File; // <--- ADD THIS
}


interface ColumnStats {
  name: string;
  type: string;
  count: number;
  nulls: number;
  unique: number;
}

interface FileUploadProps {
  onFileProcessed: (file: UploadedFile) => void;
  datasetName?: string;
}

interface FileUploadStatus {
  file: UploadedFile;
  status: 'pending' | 'uploading' | 'success' | 'error';
  error?: string;
}

export const FileUpload: React.FC<FileUploadProps> = ({ onFileProcessed, datasetName }) => {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<UploadedFile | null>(null);
  const [multipleFiles, setMultipleFiles] = useState<FileUploadStatus[]>([]);

  const detectColumnType = (values: any[]): string => {
    const nonNullValues = values.filter(v => v !== null && v !== undefined && v !== '');
    if (nonNullValues.length === 0) return 'empty';

    const sample = nonNullValues[0];
    if (!isNaN(Number(sample))) return 'number';
    if (!isNaN(Date.parse(sample))) return 'date';
    return 'string';
  };

  const calculateStats = (data: any[], columns: string[]): ColumnStats[] => {
    return columns.map(col => {
      const values = data.map(row => row[col]);
      const nonNullValues = values.filter(v => v !== null && v !== undefined && v !== '');

      return {
        name: col,
        type: detectColumnType(values),
        count: values.length,
        nulls: values.length - nonNullValues.length,
        unique: new Set(nonNullValues).size
      };
    });
  };

  const processCSV = async (file: File): Promise<UploadedFile> => {
    return new Promise((resolve, reject) => {
      Papa.parse(file, {
        header: true,
        dynamicTyping: true,
        skipEmptyLines: true,
        complete: (results) => {
          const data = results.data;
          const columns = results.meta.fields || [];
          const stats = calculateStats(data, columns);

          resolve({
            name: file.name,
            size: file.size,
            type: 'CSV',
            uploadTime: new Date(),
            data: data.slice(0, 50), // Preview first 50 rows
            columns,
            stats,
            file
          });
        },
        error: (error) => reject(error)
      });
    });
  };

  const processExcel = async (file: File): Promise<UploadedFile> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const data = new Uint8Array(e.target?.result as ArrayBuffer);
          const workbook = XLSX.read(data, { type: 'array' });
          const firstSheet = workbook.Sheets[workbook.SheetNames[0]];
          const jsonData = XLSX.utils.sheet_to_json(firstSheet);

          const columns = Object.keys(jsonData[0] || {});
          const stats = calculateStats(jsonData, columns);

          resolve({
            name: file.name,
            size: file.size,
            type: 'Excel',
            uploadTime: new Date(),
            data: jsonData.slice(0, 50),
            columns,
            stats,
            file: file
          });
        } catch (error) {
          reject(error);
        }
      };
      reader.onerror = reject;
      reader.readAsArrayBuffer(file);
    });
  };

  const processGeoJSON = async (file: File): Promise<UploadedFile> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const geojson = JSON.parse(e.target?.result as string);
          const features = geojson.features || [];
          const data = features.map((f: any) => ({
            ...f.properties,
            geometry: f.geometry
          }));

          const columns = Object.keys(data[0] || {});
          const stats = calculateStats(data, columns);

          resolve({
            name: file.name,
            size: file.size,
            type: 'GeoJSON',
            uploadTime: new Date(),
            data: data.slice(0, 50),
            columns,
            stats,
            file: file
          });
        } catch (error) {
          reject(error);
        }
      };
      reader.onerror = reject;
      reader.readAsText(file);
    });
  };

  const processXML = async (file: File): Promise<UploadedFile> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const parser = new DOMParser();
          const xmlDoc = parser.parseFromString(e.target?.result as string, 'text/xml');

          // Simple XML to JSON conversion (customize based on your XML structure)
          const elements = xmlDoc.getElementsByTagName('*');
          const data: any[] = [];

          for (let i = 0; i < Math.min(elements.length, 50); i++) {
            const element = elements[i];
            const obj: any = {};
            for (let j = 0; j < element.attributes.length; j++) {
              const attr = element.attributes[j];
              obj[attr.name] = attr.value;
            }
            if (Object.keys(obj).length > 0) {
              data.push(obj);
            }
          }

          const columns = Object.keys(data[0] || {});
          const stats = calculateStats(data, columns);

          resolve({
            name: file.name,
            size: file.size,
            type: 'XML',
            uploadTime: new Date(),
            data,
            columns,
            stats,
            file: file
          });
        } catch (error) {
          reject(error);
        }
      };
      reader.onerror = reject;
      reader.readAsText(file);
    });
  };

  const processZIP = async (file: File): Promise<UploadedFile> => {
    return new Promise(async (resolve, reject) => {
      try {
        const zip = new JSZip();
        const zipContent = await zip.loadAsync(file);

        // Find first CSV or Excel file in ZIP
        const files = Object.keys(zipContent.files);
        const dataFile = files.find(f =>
          f.endsWith('.csv') || f.endsWith('.xlsx') || f.endsWith('.xls')
        );

        if (!dataFile) {
          reject(new Error('No CSV or Excel file found in ZIP'));
          return;
        }

        const fileData = await zipContent.files[dataFile].async('blob');
        const extractedFile = new File([fileData], dataFile);

        if (dataFile.endsWith('.csv')) {
          resolve(await processCSV(extractedFile));
        } else {
          resolve(await processExcel(extractedFile));
        }
      } catch (error) {
        reject(error);
      }
    });
  };

  const processFile = async (file: File): Promise<UploadedFile> => {
    if (file.name.endsWith('.csv')) {
      return await processCSV(file);
    } else if (file.name.endsWith('.xlsx') || file.name.endsWith('.xls')) {
      return await processExcel(file);
    } else if (file.name.endsWith('.geojson') || file.name.endsWith('.json')) {
      return await processGeoJSON(file);
    } else if (file.name.endsWith('.xml')) {
      return await processXML(file);
    } else if (file.name.endsWith('.zip')) {
      return await processZIP(file);
    } else {
      throw new Error('Unsupported file format');
    }
  };

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return;

    setUploading(true);
    setError(null);
    setProgress(0);

    try {
      // Handle multiple files
      if (acceptedFiles.length > 1) {
        const processedFiles: FileUploadStatus[] = [];

        for (const file of acceptedFiles) {
          try {
            const processed = await processFile(file);
            processedFiles.push({ file: processed, status: 'pending' });
          } catch (err) {
            processedFiles.push({
              file: { name: file.name, size: file.size, type: 'Unknown', uploadTime: new Date(), data: [], columns: [], stats: [], file },
              status: 'error',
              error: err instanceof Error ? err.message : 'Failed to process'
            });
          }
        }

        setMultipleFiles(processedFiles);
        setPreview(null);
      } else {
        // Single file - use existing preview flow
        const file = acceptedFiles[0];
        const progressInterval = setInterval(() => {
          setProgress(prev => Math.min(prev + 10, 90));
        }, 200);

        const processedFile = await processFile(file);

        clearInterval(progressInterval);
        setProgress(100);
        setPreview(processedFile);
        setMultipleFiles([]);
      }

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to process file');
    } finally {
      setUploading(false);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'text/csv': ['.csv'],
      'application/vnd.ms-excel': ['.xls'],
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
      'application/json': ['.json', '.geojson'],
      'application/xml': ['.xml'],
      'application/zip': ['.zip']
    },
    maxSize: 100 * 1024 * 1024, // 100MB
    multiple: true
  });

  const handleConfirm = async () => {
    if (!preview) return;

    setUploading(true); // Reuse the loading state

    try {
      await uploadDatasetFile({
        file: preview.file,
        dataset_name: datasetName || undefined,
      });
      onFileProcessed(preview);
      setPreview(null);

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleConfirmAll = async () => {
    if (multipleFiles.length === 0) return;

    setUploading(true);

    const updatedFiles = [...multipleFiles];

    // Upload all files in parallel
    const uploadPromises = multipleFiles.map(async (fileStatus, index) => {
      if (fileStatus.status === 'error') return; // Skip already errored files

      updatedFiles[index] = { ...fileStatus, status: 'uploading' };
      setMultipleFiles([...updatedFiles]);

      try {
        await uploadDatasetFile({ file: fileStatus.file.file });
        updatedFiles[index] = { ...fileStatus, status: 'success' };
        onFileProcessed(fileStatus.file);
      } catch (err) {
        updatedFiles[index] = {
          ...fileStatus,
          status: 'error',
          error: err instanceof Error ? err.message : 'Upload failed'
        };
      }

      setMultipleFiles([...updatedFiles]);
    });

    await Promise.all(uploadPromises);
    setUploading(false);

    // Clear if all successful
    const allSuccess = updatedFiles.every(f => f.status === 'success');
    if (allSuccess) {
      setTimeout(() => setMultipleFiles([]), 2000);
    }
  };

  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  return (
    <div className="file-upload-container">
      {multipleFiles.length > 0 ? (
        <div className="multi-file-preview">
          <h3>Uploading {multipleFiles.length} files</h3>
          <div className="file-list">
            {multipleFiles.map((fileStatus, idx) => (
              <div key={idx} className={`file-item ${fileStatus.status}`}>
                <div className="file-item-info">
                  <span className="file-name">{fileStatus.file.name}</span>
                  <span className="file-size">{formatBytes(fileStatus.file.size)}</span>
                </div>
                <div className="file-item-status">
                  {fileStatus.status === 'pending' && <span className="status-pending">Pending</span>}
                  {fileStatus.status === 'uploading' && <span className="status-uploading">Uploading...</span>}
                  {fileStatus.status === 'success' && <span className="status-success">Done</span>}
                  {fileStatus.status === 'error' && <span className="status-error">{fileStatus.error}</span>}
                </div>
              </div>
            ))}
          </div>
          <div className="preview-actions">
            <button className="btn-cancel" onClick={() => setMultipleFiles([])}>
              Cancel
            </button>
            <button
              className="btn-confirm"
              onClick={handleConfirmAll}
              disabled={uploading}
            >
              {uploading ? 'Uploading...' : `Upload All (${multipleFiles.filter(f => f.status === 'pending').length} files)`}
            </button>
          </div>
        </div>
      ) : !preview ? (
        <div
          {...getRootProps()}
          className={`dropzone ${isDragActive ? 'active' : ''} ${uploading ? 'uploading' : ''}`}
        >
          <input {...getInputProps()} />
          {uploading ? (
            <div className="upload-progress">
              <div className="spinner"></div>
              <p>Processing file... {progress}%</p>
              <div className="progress-bar">
                <div className="progress-fill" style={{ width: `${progress}%` }}></div>
              </div>
            </div>
          ) : isDragActive ? (
            <p>Drop the file here...</p>
          ) : (
            <div className="upload-prompt">
              <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              <p>Drag and drop files here, or click to browse</p>
              <span className="supported-formats">
                Supports multiple files: CSV, Excel (.xlsx, .xls), GeoJSON, XML, ZIP
              </span>
            </div>
          )}
        </div>
      ) : (
        <div className="file-preview">
          <h3>File Preview: {preview.name}</h3>
          <div className="file-info">
            <p><strong>Type:</strong> {preview.type}</p>
            <p><strong>Size:</strong> {formatBytes(preview.size)}</p>
            <p><strong>Uploaded:</strong> {formatDateTimeHuman(preview.uploadTime, '—')}</p>
            <p><strong>Rows:</strong> {preview.stats[0]?.count || 0} (showing first 50)</p>
            <p><strong>Columns:</strong> {preview.columns.length}</p>
          </div>

          <h4>Column Information:</h4>
          <div className="column-stats">
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Count</th>
                  <th>Nulls</th>
                  <th>Unique</th>
                </tr>
              </thead>
              <tbody>
                {preview.stats.map((stat, idx) => (
                  <tr key={idx}>
                    <td>{stat.name}</td>
                    <td><span className={`type-badge ${stat.type}`}>{stat.type}</span></td>
                    <td>{stat.count}</td>
                    <td>{stat.nulls}</td>
                    <td>{stat.unique}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h4>Data Preview (first 50 rows):</h4>
          <div className="data-preview">
            <table>
              <thead>
                <tr>
                  {preview.columns.map((col, idx) => (
                    <th key={idx}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.data.slice(0, 10).map((row, idx) => (
                  <tr key={idx}>
                    {preview.columns.map((col, colIdx) => (
                      <td key={colIdx}>{String(row[col] || '')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="preview-actions">
            <button className="btn-cancel" onClick={() => setPreview(null)}>
              Cancel
            </button>
            <button className="btn-confirm" onClick={handleConfirm}>
              Confirm & Use This Data
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="error-message">
          <strong>Error:</strong> {error}
        </div>
      )}

      <style>{`
        .file-upload-container {
          width: 100%;
          max-width: 1200px;
          margin: 20px auto;
        }

        .dropzone {
          border: 2px dashed #667eea;
          border-radius: 8px;
          padding: 40px;
          text-align: center;
          cursor: pointer;
          transition: all 0.3s ease;
          background: rgba(102, 126, 234, 0.05);
        }

        .dropzone:hover {
          border-color: #764ba2;
          background: rgba(102, 126, 234, 0.1);
        }

        .dropzone.active {
          border-color: #764ba2;
          background: rgba(118, 75, 162, 0.1);
        }

        .dropzone.uploading {
          pointer-events: none;
          opacity: 0.7;
        }

        .upload-prompt svg {
          color: #667eea;
          margin-bottom: 16px;
        }

        .upload-prompt p {
          font-size: 18px;
          margin: 8px 0;
          color: #333;
        }

        .supported-formats {
          font-size: 14px;
          color: #666;
        }

        .upload-progress {
          padding: 20px;
        }

        .spinner {
          border: 3px solid rgba(102, 126, 234, 0.3);
          border-top: 3px solid #667eea;
          border-radius: 50%;
          width: 40px;
          height: 40px;
          animation: spin 1s linear infinite;
          margin: 0 auto 16px;
        }

        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }

        .progress-bar {
          width: 100%;
          height: 8px;
          background: rgba(102, 126, 234, 0.2);
          border-radius: 4px;
          overflow: hidden;
          margin-top: 16px;
        }

        .progress-fill {
          height: 100%;
          background: linear-gradient(90deg, #667eea, #764ba2);
          transition: width 0.3s ease;
        }

        .file-preview {
          background: white;
          border-radius: 8px;
          padding: 24px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }

        .file-preview h3 {
          margin-top: 0;
          color: #333;
        }

        .file-info {
          background: #f5f5f5;
          padding: 16px;
          border-radius: 4px;
          margin: 16px 0;
        }

        .file-info p {
          margin: 8px 0;
        }

        .column-stats table,
        .data-preview table {
          width: 100%;
          border-collapse: collapse;
          margin: 16px 0;
          font-size: 14px;
        }

        .column-stats th,
        .column-stats td,
        .data-preview th,
        .data-preview td {
          padding: 8px;
          border: 1px solid #ddd;
          text-align: left;
        }

        .column-stats th,
        .data-preview th {
          background: #667eea;
          color: white;
          font-weight: 600;
        }

        .type-badge {
          padding: 2px 8px;
          border-radius: 4px;
          font-size: 12px;
          font-weight: 600;
        }

        .type-badge.number { background: #e3f2fd; color: #1976d2; }
        .type-badge.string { background: #f3e5f5; color: #7b1fa2; }
        .type-badge.date { background: #e8f5e9; color: #388e3c; }
        .type-badge.empty { background: #fce4ec; color: #c2185b; }

        .data-preview {
          max-height: 400px;
          overflow: auto;
        }

        .preview-actions {
          display: flex;
          gap: 16px;
          justify-content: flex-end;
          margin-top: 24px;
        }

        .btn-cancel,
        .btn-confirm {
          padding: 12px 24px;
          border: none;
          border-radius: 4px;
          font-size: 16px;
          cursor: pointer;
          transition: all 0.3s ease;
        }

        .btn-cancel {
          background: #e0e0e0;
          color: #333;
        }

        .btn-cancel:hover {
          background: #d0d0d0;
        }

        .btn-confirm {
          background: linear-gradient(90deg, #667eea, #764ba2);
          color: white;
        }

        .btn-confirm:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }

        .error-message {
          background: #ffebee;
          color: #c62828;
          padding: 16px;
          border-radius: 4px;
          margin-top: 16px;
        }

        .multi-file-preview {
          background: white;
          border-radius: 8px;
          padding: 24px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }

        .multi-file-preview h3 {
          margin-top: 0;
          color: #333;
        }

        .file-list {
          margin: 16px 0;
        }

        .file-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 16px;
          border: 1px solid #e0e0e0;
          border-radius: 4px;
          margin-bottom: 8px;
          transition: all 0.3s ease;
        }

        .file-item.success {
          border-color: #4caf50;
          background: rgba(76, 175, 80, 0.05);
        }

        .file-item.error {
          border-color: #f44336;
          background: rgba(244, 67, 54, 0.05);
        }

        .file-item.uploading {
          border-color: #667eea;
          background: rgba(102, 126, 234, 0.05);
        }

        .file-item-info {
          display: flex;
          gap: 16px;
          align-items: center;
        }

        .file-name {
          font-weight: 500;
          color: #333;
        }

        .file-size {
          color: #666;
          font-size: 14px;
        }

        .status-pending {
          color: #666;
        }

        .status-uploading {
          color: #667eea;
        }

        .status-success {
          color: #4caf50;
          font-weight: 600;
        }

        .status-error {
          color: #f44336;
        }
      `}</style>
    </div>
  );
};
