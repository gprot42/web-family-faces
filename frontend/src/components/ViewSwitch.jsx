import { NavLink, useLocation } from "react-router-dom";
import { tip } from "../tip.js";

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
  const personParam = Boolean(search.get("person"));
  const tagParam = Boolean(search.get("tag"));
  const onPhotoPage = loc.pathname.startsWith("/photos/");
  const personActive = byPerson || (onPhotoPage && personParam && !tagParam);
  const tagActive = byTag || (onPhotoPage && tagParam);
  const folderActive =
    (loc.pathname === "/photos" && !byPerson && !byTag) || (onPhotoPage && !personParam && !tagParam);
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
        <NavLink
          to="/photos"
          className={() => (folderActive ? "active" : undefined)}
          {...tip("Full photos grouped by album folder.")}
        >
          Folder View
        </NavLink>
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
