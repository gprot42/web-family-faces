import { Link } from "react-router-dom";
import { tip } from "../tip.js";

export default function BrandLogo({ compact = false, linked = true, className = "" }) {
  const img = (
    <img
      className="brand-logo"
      src="/family-faces-logo.jpg"
      width="1024"
      height="1024"
      alt="Family Faces (AI)"
    />
  );
  const cls = `brand-lockup${compact ? " compact" : ""}${className ? ` ${className}` : ""}`;
  if (!linked) return <span className={cls}>{img}</span>;
  return (
    <Link
      className={cls}
      to="/"
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
      {...tip("Go back to the home page.")}
    >
      {img}
    </Link>
  );
}
