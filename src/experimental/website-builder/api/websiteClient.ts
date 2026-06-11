import { apiFetchApp } from '../../../api/http';
import { ENABLE_WEBSITE_BUILDER_ROUTES } from '../../../config';

type JsonRecord = Record<string, unknown>;

const ensureWebsiteBuilderEnabled = () => {
  if (!ENABLE_WEBSITE_BUILDER_ROUTES) {
    throw new Error(
      'Website builder is disabled (set VITE_ENABLE_WEBSITE_BUILDER_ROUTES=1 to enable when backend supports it).'
    );
  }
};

export const generateWebsite = async (payload: JsonRecord): Promise<JsonRecord> => {
  ensureWebsiteBuilderEnabled();
  const response = await apiFetchApp('/api/websites/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const createEditableWebsite = async (payload: JsonRecord): Promise<JsonRecord> => {
  ensureWebsiteBuilderEnabled();
  const response = await apiFetchApp('/api/websites/create-editable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const editWebsite = async (payload: JsonRecord): Promise<JsonRecord> => {
  ensureWebsiteBuilderEnabled();
  const response = await apiFetchApp('/api/websites/edit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const chatWebsite = async (payload: JsonRecord): Promise<JsonRecord> => {
  ensureWebsiteBuilderEnabled();
  const response = await apiFetchApp('/api/websites/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const buildWebsiteApp = async (payload: JsonRecord): Promise<JsonRecord> => {
  ensureWebsiteBuilderEnabled();
  const response = await apiFetchApp('/api/apps/build-website', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const getWebsiteByName = async (siteName: string): Promise<JsonRecord> => {
  ensureWebsiteBuilderEnabled();
  const response = await apiFetchApp(`/api/websites/by-name/${encodeURIComponent(siteName)}`);
  return response.json();
};
