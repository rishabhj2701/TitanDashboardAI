import {
  avgSpeedVideo,
  crashEditVideo,
  crashImg,
  fatalCrashVideo,
  hardBrakingImg,
  workzoneImg,
} from '../features/shared/media';

type InfoOverlayProps = {
  open: boolean;
  onClose: () => void;
};

const InfoOverlay: React.FC<InfoOverlayProps> = ({ open, onClose }) => {
  if (!open) return null;

  return (
    <div className="info-overlay" onClick={onClose}>
      <div className="info-panel" onClick={(e) => e.stopPropagation()}>
        <div className="info-header">
          <div>
            <div className="info-kicker">Traffic & Crash Intelligence</div>
            <h2>Welcome to the AI Traffic Dashboard Analyst</h2>
            <p className="info-subtitle">
              Explore connected vehicle traffic, crash data, and workzone overlays with AI-powered analysis.
            </p>
          </div>
          <button className="info-close" onClick={onClose} aria-label="Close info">✕</button>
        </div>

        <div className="info-grid">
          <div className="info-card wide">
            <h3>Current Features</h3>
            <p>
              - Crash & Hard-Braking Analysis on subset of July, 2025 CV data in St. Louis, MO.<br />
              - Some supported natural langauge queries for crash and CV data. <br />
              - Interactive Map with speed-based vehicle coloring, crash points, and hard braking identification.<br />
            </p>
            <h3>Upcoming Features</h3>
            <p>
              - Full Workzone Analysis to go along with Crash & Hard-Braking analyses (currently works but needs modification).<br />
              - AI Powered Website Builder used to create deployable websites with customized inisghts on your data.<br />
              - Large-Scale analysis on state-wide CV, crash and workzone data.<br />
              - Fully supported querying of uploaded user data including traffic signal, workzone, crash or CV data. <br />
              - Our goal is to allow users to upload their own data, so they don't have to rely on our. <br />
            </p>
            <h3>Disclaimers</h3>
            <p>
              - We are using AI to query these large datasets, it is prone to hallucinations and errors in its current state. <br />
              - Complex queries will most likely result in errors or unkown responses, but feel free to test out the system and try to break it however you please, we will appreciate your feedback! <br />
              - If you have asked the chabot a question and get an unexpected response, try opening a new tab and re-entering your query.<br />
              - Inaccuracies shown on the map are a reflection of our sample data, not necessarily the querying or mapping process (e.g., a query for I-70 crashes shows dots which aren't on I-70 directly due to the lat, lon being matched to the closest interstate).<br />
              - The current state of this application is to display the functionality and potential of an AI-powered traffic data analysis system. Please reach out and let us know if you see any obvious areas for improvement as we continue development! <br />
            </p>
          </div>

          <div className="info-card">
            <h3>Example Queries</h3>
            <ul className="info-list">
              <li>"Run the crash analysis." OR "Run the hard braking analysis."</li>
              <li>"Show top 5 roads with most fatal crashes."</li>
              <li>"Graph the average speed on I-44 vs I-70."</li>
              <li>"Show the vehicle with the highest recorded speed."</li>
              <li>"What percentage of crashes resulted in a fatality?"</li>
              <li>"Find the 5 roads with the most occurrences of speeding."</li>
              <li>If you use hard braking or crash analysis to create graphs, you may edit filters/params and regenerate.</li>
              <li>Use the map legend to interpret speeds, crashes, and workzones.</li>
            </ul>
          </div>

          <div className="info-card media video-card">
            <div className="media-badge">Demo video</div>
            <div className="media-player">
              <video src={crashEditVideo} poster={crashImg} controls playsInline preload="metadata" />
            </div>
            <div className="media-body">
              <div className="media-title">Crash Analysis Walkthrough</div>
              <p>See how our crash analysis produces editable charts and map overlays with severity coloring.</p>
            </div>
          </div>

          <div className="info-card media video-card">
            <div className="media-badge">Demo video</div>
            <div className="media-player">
              <video src={avgSpeedVideo} poster={workzoneImg} controls playsInline preload="metadata" />
            </div>
            <div className="media-body">
              <div className="media-title">Speed Comparison</div>
              <p>Average speed comparison on two major interstates.</p>
            </div>
          </div>

          <div className="info-card media video-card">
            <div className="media-badge">Demo video</div>
            <div className="media-player">
              <video src={fatalCrashVideo} poster={crashImg} controls playsInline preload="metadata" />
            </div>
            <div className="media-body">
              <div className="media-title">Querying Crash Data</div>
              <p>Using natural language to produce a map layer showing fatal crashes from the data.</p>
            </div>
          </div>

          <div className="info-card screenshot-card">
            <div className="media-badge alt">Screenshots</div>
            <div className="screenshot-grid">
              <div className="screenshot-item" onClick={(e) => {
                e.stopPropagation();
                window.open(crashImg, '_blank');
              }}>
                <div className="screenshot-image" style={{ backgroundImage: `url(${crashImg})` }} />
                <div className="screenshot-title">Crash Analysis</div>
              </div>
              <div className="screenshot-item" onClick={(e) => {
                e.stopPropagation();
                window.open(workzoneImg, '_blank');
              }}>
                <div className="screenshot-image" style={{ backgroundImage: `url(${workzoneImg})` }} />
                <div className="screenshot-title">Workzone Analysis</div>
              </div>
              <div className="screenshot-item" onClick={(e) => {
                e.stopPropagation();
                window.open(hardBrakingImg, '_blank');
              }}>
                <div className="screenshot-image" style={{ backgroundImage: `url(${hardBrakingImg})` }} />
                <div className="screenshot-title">Hard Braking Analysis</div>
              </div>
            </div>
            <div className="media-body">
              <div className="media-title">Maps & Overlays</div>
              <p>Click any screenshot to view full size. Hard braking and workzone visuals ready to share in demos. Use the language as shown in the screenshot for best results.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default InfoOverlay;
