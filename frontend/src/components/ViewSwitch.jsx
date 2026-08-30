import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { requestFolderIndex } from "../folders.js";
import { tip } from "../tip.js";

export function FolderViewLink({ children, ...rest }) {
  const loc = useLocation();
  const nav = useNavigate();
  const search = new URLSearchParams(loc.search);
  const onFolderPage =
    loc.pathname === "/photos" &&
    search.get("by") !== "person" &&
    search.get("by") !== "tag" &&
    search.get("by") !== "later";
  return (
    <NavLink
      to="/photos"
      {...rest}
      onClick={(event) => {
        rest.onClick?.(event);
        if (event.defaultPrevented) return;
        if (!onFolderPage) return;
        event.preventDefault();
        if (loc.hash || loc.search) nav("/photos", { replace: true });
        requestFolderIndex();
      }}
    >
      {children}
    </NavLink>
  );
}

function photoHref(photoId, personId) {
  if (photoId) {
    return personId ? `/photos/${photoId}?person=${personId}` : `/photos/${photoId}`;
  }
  if (personId) return `/photos?by=person&person=${personId}`;
  return "/photos?by=person";
}

export default function ViewSwitch({ photoId, personId }) {
  const loc = useLocation();
  const search = new URLSearchParams(loc.search);
  const byPerson = search.get("by") === "person";
  const byTag = search.get("by") === "tag";
  const byLater = search.get("by") === "later";
  const personParam = Boolean(search.get("person"));
  const tagParam = Boolean(search.get("tag"));
  const laterParam = Boolean(search.get("later"));
  const onPhotoPage = loc.pathname.startsWith("/photos/");
  const personActive = byPerson || (onPhotoPage && personParam && !tagParam && !laterParam);
  const tagActive = byTag || (onPhotoPage && tagParam);
  const folderActive =
    (loc.pathname === "/photos" && !byPerson && !byTag && !byLater) ||
    (onPhotoPage && !personParam && !tagParam && !laterParam);
  const peopleActive = loc.pathname.startsWith("/people");

  return (
    <div className="view-switch-wrap">
      <p className="view-switch-label">Group photos</p>
      <div className="view-switch" role="tablist" aria-label="Group photos">
        <NavLink
          to="/people"
          className={() => (peopleActive ? "active" : undefined)}
          {...tip("Identified faces stored in the database. Cropped faces only — not the photo albums.")}
        >
          Faces in DB View
        </NavLink>
        <FolderViewLink
          className={() => (folderActive ? "active" : undefined)}
          {...tip("Full photos grouped by album folder. Click again to see every folder.")}
        >
          Folder View
        </FolderViewLink>
        <NavLink
          to={photoHref(photoId, personId)}
          className={() => (personActive ? "active" : undefined)}
          {...tip("Full photos grouped by person. Each name has its own list.")}
        >
          View by person
        </NavLink>
        <NavLink
          to="/photos?by=tag"
          className={() => (tagActive ? "active" : undefined)}
          {...tip("Full photos grouped by a tag you added on the picture.")}
        >
          View by tag
        </NavLink>
      </div>
    </div>
  );
}
