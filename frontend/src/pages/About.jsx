import { Link } from "react-router-dom";

export default function About() {
  return (
    <div className="help">
      <div className="page-head">
        <div>
          <p className="eyebrow">About</p>
          <h1>About Family Faces</h1>
          <p className="lede">
            A local catalog for a family album. Faces are grouped on this machine. You name a
            person once. Originals on the NAS are never rewritten.
          </p>
        </div>
      </div>

      <section className="card help-block safety">
        <h2>Originals stay where they are</h2>
        <p>
          Photos stay in their original folders. They are never moved, renamed, or rewritten
          (including EXIF). Names live in the app database, and each album also keeps a{" "}
          <code>.photosort.json</code> so a copied folder stays labelled.{" "}
          <strong>Purge faces from database</strong> in Settings clears the app catalog only —
          those JSON files stay. Finding known faces is read-only. Close the app any time;{" "}
          <strong>Clusters to name</strong> picks up where you left off.
        </p>
      </section>

      <section className="card help-block">
        <h2>How AI is used</h2>
        <p>
          Finding and matching faces is done on this Mac. The app uses{" "}
          <a href="https://github.com/deepinsight/insightface" target="_blank" rel="noreferrer">
            InsightFace
          </a>{" "}
          <code>buffalo_l</code> (
          <a href="https://github.com/deepinsight/insightface" target="_blank" rel="noreferrer">
            ArcFace
          </a>
          ) to detect faces, group lookalikes, and match a name you already typed. Those
          embeddings, thumbnails, and the name catalog stay in the local <code>data/</code> folder.
          The 6,000-photo matcher never leaves this computer. No cloud face service is used for
          that work.
        </p>
        <p>
          <strong>Look up famous face</strong> is the only AI that talks to the internet. It is
          optional, off until you add a key or SuperGrok in{" "}
          <Link to="/settings">Settings</Link>, and it runs only when you click the button on one
          face.
        </p>
      </section>

      <section className="card help-block safety">
        <h2>What is sent, and where</h2>
        <p>
          On lookup, Family Faces posts to xAI at{" "}
          <a href="https://api.x.ai/v1/responses" target="_blank" rel="noreferrer">
            https://api.x.ai/v1/responses
          </a>{" "}
          (model <code>grok-4.6</code>; see{" "}
          <a href="https://docs.x.ai" target="_blank" rel="noreferrer">
            docs.x.ai
          </a>{" "}
          and{" "}
          <a href="https://x.ai/api" target="_blank" rel="noreferrer">
            x.ai/api
          </a>
          ). Grok may search the public web, including public portraits, to compare with that crop.
        </p>
        <ul>
          <li>
            <strong>Sent:</strong> the already-cut face crop (a small JPEG, not the original
            photo), plus short text clues — filename, album folder, dates, EXIF, camera, people
            already named in the same picture, and any reply you type (“try again”, “not this
            person”).
          </li>
          <li>
            <strong>Not sent:</strong> original files, the rest of the album, EXIF writes, the
            SQLite catalog, or other people’s full photos. The request asks xAI not to store the
            prompt.
          </li>
          <li>
            SuperGrok sign-in goes only to{" "}
            <a href="https://auth.x.ai" target="_blank" rel="noreferrer">
              https://auth.x.ai
            </a>{" "}
            /{" "}
            <a href="https://accounts.x.ai" target="_blank" rel="noreferrer">
              https://accounts.x.ai
            </a>{" "}
            for a device code (the same flow as{" "}
            <a href="https://grok.com" target="_blank" rel="noreferrer">
              grok.com
            </a>
            ). Tokens stay in the app data folder on this Mac. Create an API key at{" "}
            <a href="https://console.x.ai/team/default/api-keys" target="_blank" rel="noreferrer">
              https://console.x.ai/team/default/api-keys
            </a>
            ; Family Faces saves it in the app data folder and copies it to{" "}
            <code>~/.config/xai/api_key</code>.
          </li>
          <li>
            A suggested name is not written until you click <strong>Use this name</strong>.
          </li>
        </ul>
        <p className="hint">Version 0.1 · data stays on this Mac · originals stay on the NAS.</p>
      </section>

      <p className="hint">
        How to use the views and keyboard: <Link to="/help">Help</Link>.
      </p>
    </div>
  );
}
