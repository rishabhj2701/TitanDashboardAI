import { IngestionInspector } from '../IngestionInspector';

type IngestionOverlayProps = {
  open: boolean;
  onClose: () => void;
};

function IngestionOverlay({ open, onClose }: IngestionOverlayProps) {
  if (!open) return null;

  return (
    <div className="ingestion-overlay" onClick={onClose}>
      <div className="ingestion-modal" onClick={(event) => event.stopPropagation()}>
        <IngestionInspector onClose={onClose} />
      </div>
    </div>
  );
}

export default IngestionOverlay;
