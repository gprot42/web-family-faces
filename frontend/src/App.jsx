import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import Dashboard from "./pages/Dashboard.jsx";
import Photos from "./pages/Photos.jsx";
import PhotoDetail from "./pages/PhotoDetail.jsx";
import Clusters from "./pages/Clusters.jsx";
import People from "./pages/People.jsx";
import PersonDetail from "./pages/PersonDetail.jsx";
import Search from "./pages/Search.jsx";
import Review from "./pages/Review.jsx";
import Help from "./pages/Help.jsx";
import About from "./pages/About.jsx";
import Settings from "./pages/Settings.jsx";
import Tree from "./pages/Tree.jsx";
import TipLayer from "./components/TipLayer.jsx";
import PhotoMenu from "./components/PhotoMenu.jsx";
import BrandLogo from "./components/BrandLogo.jsx";
import PeopleSearch from "./components/PeopleSearch.jsx";
import { tip } from "./tip.js";
import { FolderViewLink } from "./components/ViewSwitch.jsx";
import { CATALOG_CHANGE_EVENT, PHOTO_CHANGE_EVENT } from "./photoMenu.js";
import { hasLaterReviewTag } from "./photoTags.js";

function navActive(on) {
  return on ? "active" : undefined;
}

export default function App() {
  const loc = useLocation();
  const search = new URLSearchParams(loc.search);
  const byPerson = search.get("by") === "person";
  const byTag = search.get("by") === "tag";
  const byLater = search.get("by") === "later";
  const personQ = Boolean(search.get("person"));
  const tagQ = Boolean(search.get("tag"));
  const laterQ = Boolean(search.get("later"));
  const onPhotoList = loc.pathname === "/photos";
  const onPhotoDetail = loc.pathname.startsWith("/photos/");
  const laterNav = (onPhotoList && byLater) || (onPhotoDetail && laterQ);
  const folderNav =
    (onPhotoList && !byPerson && !byTag && !byLater) || (onPhotoDetail && !personQ && !tagQ && !laterQ);
  const peopleNav = loc.pathname.startsWith("/people") || (onPhotoDetail && personQ);
  const [stats, setStats] = useState(null);
  const [laterReviewCount, setLaterReviewCount] = useState(0);
  const [jobs, setJobs] = useState(null);

  const statsPending = useRef(false);
  const statsAgain = useRef(false);
  const statsTimer = useRef(null);

  function adjustStats(delta) {
    if (!delta || typeof delta !== "object") return;
    setStats((cur) => {
      if (!cur) return cur;
      const next = { ...cur };
      for (const [key, value] of Object.entries(delta)) {
        if (typeof value === "number") next[key] = Math.max(0, (Number(next[key]) || 0) + value);
      }
      return next;
    });
  }

  async function refreshLater() {
    try {
      const listed = await api.photoTags();
      const hit = (listed.items || []).find((item) => hasLaterReviewTag([item.tag]));
      setLaterReviewCount(Number(hit?.photos) || 0);
    } catch {
      /* keep the last count */
    }
  }

  async function refresh() {
    if (statsPending.current) {
      statsAgain.current = true;
      return;
    }
    statsPending.current = true;
    try {
      setStats(await api.stats());
    } catch {
      /* keep the last good counts — a failed poll used to wipe the badges */
    }
    await refreshLater();
    statsPending.current = false;
    if (statsAgain.current) {
      statsAgain.current = false;
      refresh();
    }
  }

  function scheduleRefresh(ms = 400) {
    window.clearTimeout(statsTimer.current);
    statsTimer.current = window.setTimeout(refresh, ms);
  }

  function onChange(delta, mode) {
    if (delta && typeof delta === "object" && !delta.nativeEvent) {
      if (mode === "set") {
        setStats((cur) => (cur ? { ...cur, ...delta } : cur));
      } else {
        adjustStats(delta);
      }
      scheduleRefresh(400);
      return;
    }
    refresh();
  }

  useEffect(() => {
    scheduleRefresh(200);
  }, [loc.pathname]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 20000);
    function onCatalog() {
      scheduleRefresh(400);
    }
    function onVisible() {
      if (document.visibilityState === "visible") scheduleRefresh(400);
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onCatalog);
    window.addEventListener(CATALOG_CHANGE_EVENT, onCatalog);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      window.clearTimeout(statsTimer.current);
      window.removeEventListener(PHOTO_CHANGE_EVENT, onCatalog);
      window.removeEventListener(CATALOG_CHANGE_EVENT, onCatalog);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  useEffect(() => {
    let cancel = false;
    let timer = 0;
    const wasBusy = { current: false };
    async function tick() {
      try {
        const data = await api.jobs();
        if (cancel) return;
        setJobs(data);
        const busy = Boolean(data?.active || (data?.photo_matches || []).length);
        if (busy || wasBusy.current) refresh();
        wasBusy.current = busy;
        timer = window.setTimeout(tick, busy ? 2000 : 8000);
      } catch {
        if (!cancel) timer = window.setTimeout(tick, 8000);
      }
    }
    tick();
    return () => {
      cancel = true;
      window.clearTimeout(timer);
    };
  }, []);

  const activeJob = jobs?.active;
  const photoMatches = jobs?.photo_matches || [];
  const jobTitle = {
    pipeline: "Finding known faces",
    import: "Reading folder",
    scan: "Finding known faces",
    cluster: "Grouping faces",
    match: "Applying names",
    identify: "Identifying faces",
    verify: "Checking photos",
  };
  const jobBusy = Boolean(activeJob || photoMatches.length);
  const jobBusyLabel = activeJob
    ? jobTitle[activeJob.type] || "Working"
    : photoMatches.length === 1
      ? "Re-identifying a photo"
      : photoMatches.length
        ? `Re-identifying ${photoMatches.length} photos`
        : "";

  return (
    <div className={`app${onPhotoDetail ? " photo-page-app" : ""}`}>
      <TipLayer />
      <PhotoMenu />
      <aside className="nav">
        <BrandLogo compact className="app-brand" />
        <p className="brand-sub">Name a face once</p>
        <nav className="nav-links">
          <NavLink to="/" end {...tip("Folder summary: how many people and faces are named, and start a scan.")}>
            Summary
          </NavLink>
          <FolderViewLink
            className={() => navActive(folderNav)}
            aria-current={folderNav ? "page" : undefined}
            {...tip("Folder view: photos with names on each person. Click again for the folder list.")}
          >
            Folder View
          </FolderViewLink>
          <NavLink
            to="/photos?by=later"
            className={() => navActive(laterNav)}
            aria-current={laterNav ? "page" : undefined}
            {...tip("Photos you tagged for later review. Right-click a picture to add one.")}
          >
            Later review
            {(stats?.later_review ?? laterReviewCount) ? (
              <span className="nav-count">{stats?.later_review ?? laterReviewCount}</span>
            ) : null}
          </NavLink>
          <NavLink
            to="/to-name"
            {...tip("Clusters of unnamed faces that look like one person. Name a cluster once instead of photo by photo.")}
          >
            Clusters to name
            {stats?.unknown_clusters ? (
              <span className="nav-count">{stats.unknown_clusters}</span>
            ) : null}
          </NavLink>
          <NavLink
            to="/review"
            {...tip("Faces the matcher named. Keep the right ones. Reject the rest.")}
          >
            Check names
            {stats?.faces_auto ? <span className="nav-count">{stats.faces_auto}</span> : null}
          </NavLink>
          <NavLink
            to="/people"
            className={() => navActive(peopleNav)}
            aria-current={peopleNav ? "page" : undefined}
            {...tip("Identified faces stored in the database. Open a person, or join child and adult identities.")}
          >
            Faces in DB View
            {stats?.people ? <span className="nav-count">{stats.people}</span> : null}
          </NavLink>
          <NavLink
            to="/tree"
            {...tip("Open a GEDCOM .ged family tree and look up parents, spouses, and children.")}
          >
            Family tree
          </NavLink>
          <NavLink to="/settings" {...tip("Paste an xAI key so Look up famous face can run.")}>
            Settings
          </NavLink>
        </nav>
        <div className="nav-foot">
          {jobBusy ? (
            <Link
              to="/"
              className="nav-job"
              {...tip("A task is running in the background. You can keep browsing. Open Summary for details.")}
            >
              <span className="nav-job-dot" aria-hidden="true" />
              <span>{jobBusyLabel}…</span>
            </Link>
          ) : null}
          <NavLink to="/about" {...tip("Why originals stay untouched, and where names are stored.")}>
            About
          </NavLink>
          <NavLink to="/help" {...tip("How the app works, keyboard shortcuts, and how originals are protected.")}>
            Help
          </NavLink>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard stats={stats} jobs={jobs} onJobs={setJobs} onChange={onChange} />} />
          <Route path="/photos" element={<Photos />} />
          <Route path="/photos/:id" element={<PhotoDetail />} />
          <Route path="/to-name" element={<Clusters stats={stats} onChange={onChange} />} />
          <Route path="/clusters" element={<Navigate to="/to-name" replace />} />
          <Route path="/search" element={<Search />} />
          <Route path="/review" element={<Review onChange={onChange} />} />
          <Route path="/people" element={<People />} />
          <Route path="/people/:id" element={<PersonDetail />} />
          <Route path="/tree" element={<Tree />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/about" element={<About />} />
          <Route path="/help" element={<Help />} />
        </Routes>
      </main>
      <PeopleSearch />
    </div>
  );
}
