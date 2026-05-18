import { useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import './Map.css';
import { MAPBOX_TOKEN } from '../config';

// Mapbox token
mapboxgl.accessToken = MAPBOX_TOKEN;

interface MapProps {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  onMapReady?: (map: mapboxgl.Map) => void;
}

const Map: React.FC<MapProps> = ({ mapRef, onMapReady }) => {
  const mapContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!mapContainerRef.current) return;

    const map = new mapboxgl.Map({
      container: mapContainerRef.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [-92.5, 38.5],
      zoom: 6.5,
      preserveDrawingBuffer: true, // Required for screenshot functionality
    });

    mapRef.current = map;

    const notifyReady = () => {
      onMapReady?.(map);
    };

    if (onMapReady) {
      if (map.loaded()) {
        notifyReady();
      } else {
        map.once('load', notifyReady);
      }
    }

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [mapRef, onMapReady]);

  return <div ref={mapContainerRef} className="map-container" />;
};

export default Map;
