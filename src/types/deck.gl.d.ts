/**
 * Type declarations for deck.gl packages
 * These extend the built-in types with any missing declarations
 */

declare module '@deck.gl/core' {
  export class Deck {
    constructor(props: any);
    setProps(props: any): void;
    finalize(): void;
  }

  // Note: MapViewState and PickingInfo are not exported in deck.gl v9
  // Define them locally where needed
}

declare module '@deck.gl/layers' {
  export class ScatterplotLayer<D = any> {
    constructor(props: any);
  }

  export class IconLayer<D = any> {
    constructor(props: any);
  }

  export class ArcLayer<D = any> {
    constructor(props: any);
  }

  export class LineLayer<D = any> {
    constructor(props: any);
  }

  export class PathLayer<D = any> {
    constructor(props: any);
  }

  export class PolygonLayer<D = any> {
    constructor(props: any);
  }

  export class GeoJsonLayer<D = any> {
    constructor(props: any);
  }

  export class TextLayer<D = any> {
    constructor(props: any);
  }
}

declare module '@deck.gl/aggregation-layers' {
  export class HexagonLayer<D = any> {
    constructor(props: any);
  }

  export class HeatmapLayer<D = any> {
    constructor(props: any);
  }

  export class GridLayer<D = any> {
    constructor(props: any);
  }

  export class ScreenGridLayer<D = any> {
    constructor(props: any);
  }

  export class ContourLayer<D = any> {
    constructor(props: any);
  }

  export class CPUGridLayer<D = any> {
    constructor(props: any);
  }
}

declare module '@deck.gl/geo-layers' {
  export class TileLayer<D = any> {
    constructor(props: any);
  }

  export class MVTLayer<D = any> {
    constructor(props: any);
  }

  export class TerrainLayer<D = any> {
    constructor(props: any);
  }

  export class TripsLayer<D = any> {
    constructor(props: any);
  }

  export class H3HexagonLayer<D = any> {
    constructor(props: any);
  }
}

declare module '@deck.gl/mapbox' {
  import type { Map as MapboxMap, IControl } from 'mapbox-gl';

  export class MapboxOverlay {
    constructor(props: {
      layers?: any[];
      interleaved?: boolean;
      getTooltip?: (info: any) => any;
    });

    setProps(props: any): void;
    finalize(): void;

    // IControl methods
    onAdd(map: MapboxMap): HTMLElement;
    onRemove(map: MapboxMap): void;
    getDefaultPosition?(): string;
  }

  export class MapboxLayer<D = any> {
    constructor(props: any);
  }
}

declare module '@luma.gl/core' {
  // Basic luma.gl types if needed
  export const GL: any;
}

declare module 'supercluster' {
  interface Options<P, C> {
    radius?: number;
    maxZoom?: number;
    minZoom?: number;
    minPoints?: number;
    extent?: number;
    nodeSize?: number;
    log?: boolean;
    generateId?: boolean;
    reduce?: (accumulated: C, props: P) => void;
    map?: (props: P) => C;
  }

  interface ClusterProperties {
    cluster: true;
    cluster_id: number;
    point_count: number;
    point_count_abbreviated: string | number;
  }

  interface PointFeature<P> {
    type: 'Feature';
    geometry: {
      type: 'Point';
      coordinates: [number, number];
    };
    properties: P;
  }

  interface ClusterFeature<C> {
    type: 'Feature';
    geometry: {
      type: 'Point';
      coordinates: [number, number];
    };
    properties: ClusterProperties & C;
  }

  export default class Supercluster<P = any, C = any> {
    constructor(options?: Options<P, C>);
    load(points: PointFeature<P>[]): Supercluster<P, C>;
    getClusters(
      bbox: [number, number, number, number],
      zoom: number
    ): (ClusterFeature<C> | PointFeature<P>)[];
    getClusterExpansionZoom(clusterId: number): number;
    getLeaves(
      clusterId: number,
      limit?: number,
      offset?: number
    ): PointFeature<P>[];
    getChildren(
      clusterId: number
    ): (ClusterFeature<C> | PointFeature<P>)[];
  }
}
