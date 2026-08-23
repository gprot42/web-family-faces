import { useEffect, useState } from "react";
import { api } from "../api";
import ConfirmAsk from "../components/ConfirmAsk.jsx";
import { readImportFolders } from "../folders.js";
import {
  applyFullscreenLabelsDefault,
  applyNametag,
  NAMETAG_PLACEMENTS,
  readFullscreenLabels,
  readNametag,
} from "../nametag.js";
import { applyTheme, readTheme, THEMES } from "../theme.js";
import {
  applyPlayIntervalMs,
  PLAY_INTERVAL_MAX_MS,
  PLAY_INTERVAL_MIN_MS,
  readPlayIntervalMs,
} from "../play.js";
import { tip } from "../tip.js";

export default function Settings() {
  const [theme, setTheme] = useState(() => readTheme());
  const [nametag, setNametag] = useState(() => readNametag());
  const [fullscreenLabels, setFullscreenLabels] = useState(() => readFullscreenLabels());
  const [playSeconds, setPlaySeconds] = useState(() => readPlayIntervalMs() / 1000);
  const [info, setInfo] = useState(null);
  const [folders, setFolders] = useState([]);
  const [resetFolders, setResetFolders] = useState([]);
  const [key, setKey] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [purgeAsk, setPurgeAsk] = useState(null);
  const [oauthBusy, setOauthBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  async function load() {
    const saved = readImportFolders();
    const [next, listed] = await Promise.all([
      api.settings(),
      api.nameFolders(saved).catch(() => ({ items: [] })),
    ]);
    setInfo(next);
    setFolders(listed.items || []);
  }

  useEffect(() => {
    load().catch((ex) => setErr(ex.message));
  }, []);

  useEffect(() => {
    const pending = folders.some((f) => !(f.photos > 0));
    if (!pending) return undefined;
    const id = setInterval(() => {
      api
        .nameFolders(readImportFolders())
        .then((listed) => setFolders(listed.items || []))
        .catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [folders]);

  useEffect(() => {
    if (!info?.oauth_pending) return undefined;
    const ms = Math.max(2, Number(info.oauth_interval) || 5) * 1000;
    const id = setInterval(() => {
      api
        .oauthPoll()
        .then((next) => {
          setInfo((cur) => ({ ...(cur || {}), ...next }));
          if (next.status === "ok") setOk(next.message || "Signed in with SuperGrok.");
          if (next.status === "denied" || next.status === "expired" || next.status === "error") {
            setErr(next.message || "SuperGrok sign-in did not finish.");
          }
        })
        .catch((ex) => setErr(ex.message));
    }, ms);
    return () => clearInterval(id);
  }, [info?.oauth_pending, info?.oauth_interval]);

  async function saveLibraryOptions(next) {
    setErr("");
    setOk("");
    try {
      const saved = await api.saveSettings({
        auto_update: next.auto_update ?? info?.auto_update !== false,
        auto_scan_new: next.auto_scan_new ?? info?.auto_scan_new !== false,
        name_sex_check: next.name_sex_check ?? info?.name_sex_check !== false,
        folders: readImportFolders(),
      });
      setInfo(saved);
      if (next.name_sex_check === false) {
        setOk("Male / female name checking is off. Auto-names ignore those face guesses.");
      } else if (next.name_sex_check === true) {
        setOk("Male / female name checking is on.");
      } else if (!saved.auto_update) {
        setOk("Auto-update is off. New photos wait until you run Find Known Faces.");
      } else if (saved.auto_scan_new) {
        setOk("Auto-update is on. New photos will be scanned for faces.");
      } else {
        setOk("Auto-update is on. New photos are added without face scanning.");
      }
    } catch (ex) {
      setErr(ex.message);
    }
  }

  async function save(e) {
    e.preventDefault();
    setErr("");
    setOk("");
    setBusy(true);
    try {
      const next = await api.saveSettings({ xai_api_key: key.trim() });
      setInfo(next);
      setKey("");
      setShow(false);
      setOk(next.warning || "Key saved on this Mac.");
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setErr("");
    setOk("");
    setBusy(true);
    try {
      const next = await api.saveSettings({ clear_xai_key: true });
      setInfo(next);
      setKey("");
      setShow(false);
      setOk("Saved key removed.");
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleShow() {
    setErr("");
    if (show) {
      setShow(false);
      return;
    }
    if (!key && info?.xai_key_set) {
      setBusy(true);
      try {
        const next = await api.settings(true);
        setInfo(next);
        if (next.xai_api_key) setKey(next.xai_api_key);
      } catch (ex) {
        setErr(ex.message);
        return;
      } finally {
        setBusy(false);
      }
    }
    setShow(true);
  }

  async function startOauth() {
    setErr("");
    setOk("");
    setOauthBusy(true);
    try {
      const next = await api.oauthStart();
      setInfo((cur) => ({ ...(cur || {}), ...next, oauth_pending: true }));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setOauthBusy(false);
    }
  }

  async function cancelOauth() {
    setErr("");
    const next = await api.oauthCancel();
    setInfo((cur) => ({ ...(cur || {}), ...next, oauth_pending: false }));
  }

  async function openBrowser(browser) {
    setErr("");
    try {
      localStorage.setItem("photosort-browser", browser);
      await api.oauthOpen(browser);
    } catch (ex) {
      setErr(ex.message);
    }
  }

  async function newOauthCode() {
    await cancelOauth();
    await startOauth();
  }

  async function resetNames(folder) {
    const picked = Array.isArray(folder) ? folder.filter(Boolean) : folder ? [folder] : [];
    const all = !picked.length;
    const label = picked.join(", ");
    setPurgeAsk(null);
    setErr("");
    setOk("");
    setBusy(true);
    try {
      const result = await api.resetNames(all ? undefined : picked);
      try {
        sessionStorage.removeItem("photosort-lookups");
        sessionStorage.removeItem("photosort-merge-ignore");
      } catch {
        /* ignore */
      }
      const listed = await api.nameFolders().catch(() => ({ items: [] }));
      setFolders(listed.items || []);
      if (all) {
        setOk(
          `Purged the database. Cleared ${result.people_removed || 0} people. Photos and .photosort.json were not touched.`,
        );
      } else {
        setOk(
          `Purged ${picked.length === 1 ? `“${label}”` : `${picked.length} folders`} from the database. Cleared ${result.people_removed || 0} people who only appeared there. Photos and .photosort.json were not touched.`,
        );
        setResetFolders([]);
      }
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  async function signOutOauth() {
    setErr("");
    setOk("");
    const next = await api.oauthSignOut();
    setInfo(await api.settings());
    setOk(next.message || "Signed out of SuperGrok.");
  }

  const savedHere = info?.xai_key_source === "settings";
  const oauthReady = Boolean(info?.oauth_signed_in);

  return (
    <div>
      {purgeAsk ? (
        <ConfirmAsk
          danger
          title={
            !purgeAsk.length
              ? "Purge ALL names from the database?"
              : purgeAsk.length === 1
                ? `Purge names for “${purgeAsk[0]}” only?`
                : `Purge names in ${purgeAsk.length} folders?`
          }
          body={
            !purgeAsk.length
              ? "Photo files and each album’s .photosort.json stay. A later Find Known Faces can restore names from those files."
              : "Other folders stay. Photo files and .photosort.json stay."
          }
          onCancel={() => setPurgeAsk(null)}
          onConfirm={() => resetNames(purgeAsk)}
        />
      ) : null}
      <div className="page-head">
        <div>
          <p className="eyebrow">Settings</p>
          <h1>Keys and lookup</h1>
          <p className="lede">
            Pick a theme and where names sit on a photo, choose whether listed albums update on
            their own, then add SuperGrok or an xAI key for famous-face lookup. Keys stay on this
            Mac, not in the photo catalog backup, and never go into the album.
          </p>
        </div>
      </div>

      <section className="card help-block settings-card">
        <h2>Theme</h2>
        <p>Present is the current warm paper look. Light is a plain page. Tokyo Night is dark.</p>
        <div className="theme-grid" role="radiogroup" aria-label="Theme">
          {THEMES.map((item) => {
            const selected = theme === item.id;
            return (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={selected}
                className={`theme-choice ${selected ? "active" : ""}`}
                onClick={() => setTheme(applyTheme(item.id))}
                {...tip(item.hint)}
              >
                <span className="theme-swatch" aria-hidden="true">
                  {item.swatch.map((color) => (
                    <span key={color} style={{ background: color }} />
                  ))}
                </span>
                <strong>{item.label}</strong>
              </button>
            );
          })}
        </div>
      </section>

      <section className="card help-block settings-card">
        <h2>Name on the photo</h2>
        <p>Where the name sits relative to each person. Applies on Folder View and when you open a photo.</p>
        <label
          className="settings-check"
          {...tip("Show name labels on each person when you open a photo. Turn off for a clean picture. You can still Hide labels / Show labels on the photo.")}
        >
          <input
            type="checkbox"
            checked={fullscreenLabels}
            onChange={(e) => setFullscreenLabels(applyFullscreenLabelsDefault(e.target.checked))}
          />
          <span>Show name labels on photos</span>
        </label>
        <div className="theme-grid nametag-grid" role="radiogroup" aria-label="Name position">
          {NAMETAG_PLACEMENTS.map((item) => {
            const selected = nametag === item.id;
            return (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={selected}
                className={`theme-choice ${selected ? "active" : ""}`}
                onClick={() => setNametag(applyNametag(item.id))}
                {...tip(item.hint)}
              >
                <span className={`nametag-preview place-${item.id}`} aria-hidden="true">
                  <span className="np-body" />
                  <span className="np-head" />
                  <span className="np-tag">Name</span>
                </span>
                <strong>{item.label}</strong>
              </button>
            );
          })}
        </div>
      </section>

      <section className="card help-block settings-card">
        <h2>Play album</h2>
        <p>When you press Play, each photo stays on screen for this long before the next one.</p>
        <label className="settings-field" htmlFor="play-seconds">
          Seconds on each photo
        </label>
        <div className="settings-number-row">
          <input
            id="play-seconds"
            type="number"
            min={PLAY_INTERVAL_MIN_MS / 1000}
            max={PLAY_INTERVAL_MAX_MS / 1000}
            step="0.5"
            value={playSeconds}
            onChange={(e) => {
              const sec = Number(e.target.value);
              if (!Number.isFinite(sec)) return;
              setPlaySeconds(sec);
              if (sec >= PLAY_INTERVAL_MIN_MS / 1000 && sec <= PLAY_INTERVAL_MAX_MS / 1000) {
                applyPlayIntervalMs(sec * 1000);
              }
            }}
            onBlur={() => {
              const next = applyPlayIntervalMs(playSeconds * 1000);
              setPlaySeconds(next / 1000);
            }}
            {...tip("How long each picture stays on screen during Play. Default is 2.5 seconds.")}
          />
          <span className="hint">1–30 seconds · default 2.5</span>
        </div>
      </section>

      <section className="card help-block settings-card">
        <h2>Auto-update albums</h2>
        <p>
          Look for new photos in the folders you chose on Summary, including albums on the NAS.
          Files stay where they are. Turn off face scanning if you only want them listed, then run
          Find Known Faces later.
        </p>
        <label
          className="settings-check"
          {...tip("Every few minutes, walk listed albums and add photos the catalog has not seen yet.")}
        >
          <input
            type="checkbox"
            checked={info?.auto_update !== false}
            onChange={(e) => saveLibraryOptions({ auto_update: e.target.checked })}
          />
          <span>Auto-update listed folders</span>
        </label>
        <p className="hint">
          Uses the same albums as Find Known Faces. It lists files to find new ones; it does not
          re-scan every old photo for faces. New files appear in Folder View when they are found.
        </p>
        <label
          className={`settings-check nested${info?.auto_update === false ? " dim" : ""}`}
          {...tip(
            "When on, new photos are searched for faces and auto-named from the catalog. Turn off to skip AI tagging and face scanning.",
          )}
        >
          <input
            type="checkbox"
            checked={info?.auto_scan_new !== false}
            disabled={info?.auto_update === false}
            onChange={(e) => saveLibraryOptions({ auto_scan_new: e.target.checked })}
          />
          <span>Find faces on new photos</span>
        </label>
        <p className="hint nested">
          Turn this off to add new files without AI tagging or face scanning. Names already in the
          catalog are not applied until you run Find Known Faces.
        </p>
      </section>

      <section className="card help-block settings-card">
        <h2>Male / female name check</h2>
        <p>
          Find Known Faces can skip a name when the first name is usually male or female and the
          face was guessed the other way. The guess is often wrong. Names you type yourself are
          never blocked.
        </p>
        <label
          className="settings-check"
          {...tip(
            "When on, a face guessed female is not auto-named James, and the reverse. Turn off to ignore those guesses.",
          )}
        >
          <input
            type="checkbox"
            checked={info?.name_sex_check !== false}
            onChange={(e) => saveLibraryOptions({ name_sex_check: e.target.checked })}
          />
          <span>Check first names against male / female face guesses</span>
        </label>
        <p className="hint">
          Turn this off if someone is skipped because the detector guessed the wrong sex.
        </p>
      </section>

      <section className="card help-block settings-card">
        <h2>SuperGrok Heavy</h2>
        <p>
          Sign in with the SuperGrok account you use on grok.com. Type the code in the browser you
          open. A wrong try spends the old link — use <strong>New code</strong>.
        </p>
        {oauthReady ? (
          <>
            <p>
              Signed in{info.oauth_email ? ` as ${info.oauth_email}` : ""}. Famous-face lookup will
              use this session.
            </p>
            <button type="button" className="secondary" onClick={signOutOauth}>
              Sign out
            </button>
          </>
        ) : info?.oauth_pending ? (
          <>
            <div className="oauth-bar">
              <p className="oauth-code">{info.oauth_user_code}</p>
              <div className="oauth-tools">
                <button type="button" className="secondary" onClick={newOauthCode} disabled={oauthBusy}>
                  New code
                </button>
                <button type="button" className="ghost" onClick={cancelOauth}>
                  Cancel
                </button>
              </div>
            </div>
            <p className="cluster-label">Open in</p>
            <div className="oauth-browsers">
              {(info.oauth_browsers || [
                { id: "brave", label: "Brave", available: true },
                { id: "chrome", label: "Chrome", available: true },
                { id: "firefox", label: "Firefox", available: true },
                { id: "safari", label: "Safari", available: true },
              ]).map((b) => (
                <button
                  key={b.id}
                  type="button"
                  className={b.id === "brave" ? "" : "secondary"}
                  disabled={b.available === false}
                  onClick={() => openBrowser(b.id)}
                  {...tip(
                    b.available === false
                      ? `${b.label} is not installed.`
                      : `Open the device page in ${b.label}. Type the code shown above.`,
                  )}
                >
                  {b.label}
                </button>
              ))}
            </div>
          </>
        ) : (
          <button type="button" disabled={oauthBusy} onClick={startOauth}>
            {oauthBusy ? "Starting…" : "Sign in with SuperGrok"}
          </button>
        )}
      </section>

      <section className="card help-block settings-card">
        <h2>xAI API key</h2>
        <p>
          Create a key on the{" "}
          <a href="https://console.x.ai/team/default/api-keys" target="_blank" rel="noreferrer">
            API keys
          </a>{" "}
          page at console.x.ai. It should start with <code>xai-</code>. A Grok website login is
          not the same thing. Family Faces uses it only when you click{" "}
          <strong>Look up famous face</strong>. The key is saved in the Family Faces data folder
          {info?.xai_key_path ? (
            <>
              {" "}
              (<code>{info.xai_key_path}</code>)
            </>
          ) : null}{" "}
          so it survives restarts and stays out of the photo album.
        </p>
        <form onSubmit={save}>
          <label className="cluster-label" htmlFor="xai-key">
            Paste key
          </label>
          <div className="row">
            <input
              id="xai-key"
              className="grow"
              type={show ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              placeholder={info?.xai_key_set ? "Key is saved — paste a new one to replace it" : "xai-…"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
            <button
              type="button"
              className="secondary"
              disabled={busy || (!key && !info?.xai_key_set)}
              onClick={toggleShow}
              {...tip(show ? "Hide the key on this screen." : "Show the xAI key saved on this Mac.")}
            >
              {show ? "Hide key" : "Show key"}
            </button>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button
              type="submit"
              disabled={busy || !key.trim()}
              {...tip(`Keep this key in ${info?.xai_key_path || "the Family Faces data folder"}. It is reused after restarts.`)}
            >
              {busy ? "Saving…" : "Save key"}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy || !savedHere}
              onClick={remove}
              {...tip("Forget the key saved in Settings. Environment keys are left alone.")}
            >
              Remove saved key
            </button>
          </div>
        </form>
        {info?.xai_key_source === "settings" || info?.xai_key_source === "environment" ? (
          <p className="hint" style={{ marginTop: 12 }}>
            A key is ready{info.xai_key_hint ? ` (${info.xai_key_hint})` : ""}.
            {info.xai_key_source === "environment"
              ? " It is coming from the XAI_API_KEY environment variable."
              : ` It is saved in ${info.xai_key_path || "the Family Faces data folder"} and kept after restarts.`}
          </p>
        ) : info?.oauth_signed_in || info?.lookup_available ? (
          <p className="hint" style={{ marginTop: 12 }}>
            Famous-face lookup is using your SuperGrok session. You can still paste an API key to
            keep lookup working if that session expires.
          </p>
        ) : (
          <p className="hint" style={{ marginTop: 12 }}>
            No API key saved yet. Look up famous face stays off until you add a key or sign in with
            SuperGrok.
          </p>
        )}
        {ok ? <p className="hint" style={{ marginTop: 8 }}>{ok}</p> : null}
        {err ? <p className="error">{err}</p> : null}
      </section>

      <section className="card help-block settings-card">
        <h2>Purge faces from database</h2>
        <p>
          This clears names from the app database only. Photo files stay where they are. Face
          boxes stay. Statues you marked <strong>Not a person</strong> stay hidden. Each album’s{" "}
          <code>.photosort.json</code> is left as it is — a later Find Known Faces can put those names
          back.
        </p>
        <h3 className="cluster-label">All folders</h3>
        <p className="hint">
          Purges every name in the database, including family names, emperors, and every Unknown. To
          name starts over until names are restored from the JSON files.
        </p>
        <button
          type="button"
          className="danger"
          disabled={busy}
          onClick={() => setPurgeAsk([])}
          {...tip("Clear names from the database in every folder. Photo files and .photosort.json stay.")}
        >
          {busy ? "Purging…" : "Purge faces from database"}
        </button>
        <h3 className="cluster-label" style={{ marginTop: 18 }}>
          Chosen folders
          {folders.length ? (
            <span className="hint">
              {" "}
              · {folders.length} album{folders.length === 1 ? "" : "s"}
              {folders.some((f) => f.photos > 0)
                ? ` · ${folders.filter((f) => f.photos > 0).length} in the catalog`
                : ""}
            </span>
          ) : null}
        </h3>
        <p className="hint">
          Every album under the folders you selected. Purge names from the database only in the
          albums you tick. People who also appear in other folders keep their name there. JSON files
          stay.
        </p>
        <div className="reset-folders" role="group" aria-label="Folders to reset">
          {folders.map((f) => {
            const checked = resetFolders.includes(f.folder);
            const scanned = (f.photos || 0) > 0;
            return (
              <label key={f.path || f.folder} className={`reset-folder ${checked ? "picked" : ""}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={busy || !scanned}
                  onChange={() => {
                    setResetFolders((cur) =>
                      cur.includes(f.folder) ? cur.filter((name) => name !== f.folder) : [...cur, f.folder],
                    );
                  }}
                />
                <span>
                  {f.folder}
                  <span className="hint">
                    {scanned
                      ? ` · ${f.named_faces} named · ${f.photos} photo${f.photos === 1 ? "" : "s"}`
                      : " · not in the catalog yet"}
                  </span>
                </span>
              </label>
            );
          })}
          {!folders.length ? <p className="hint">No albums selected yet. Choose folders on the home page.</p> : null}
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button
            type="button"
            className="secondary"
            disabled={busy || !resetFolders.length}
            onClick={() => setPurgeAsk(resetFolders)}
            {...tip("Purge names from the database only in the ticked folders. .photosort.json stays.")}
          >
            {resetFolders.length > 1
              ? `Purge ${resetFolders.length} folders from database`
              : "Purge these folders from database"}
          </button>
        </div>
      </section>
    </div>
  );
}
