import { useState, useCallback } from 'react';
import { getDataQuality, type DataQualityResponse } from '../api/dataQualityClient';

export const useDataQuality = () => {
  const [data, setData] = useState<DataQualityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getDataQuality();
      setData(result);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load data quality';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  return { data, loading, error, refresh };
};
