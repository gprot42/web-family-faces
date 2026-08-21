import { Link } from "react-router-dom";

function Arrow() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path
        d="M12.75 4.5 7 10l5.75 5.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.85"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function BackButton({ to, onClick, overlay, children = "Back", ...rest }) {
  const className = `back-btn${overlay ? " overlay" : ""}`;
  const body = (
    <>
      <Arrow />
      <span>{children}</span>
    </>
  );
  if (to) {
    return (
      <Link className={className} to={to} onClick={onClick} {...rest}>
        {body}
      </Link>
    );
  }
  return (
    <button type="button" className={className} onClick={onClick} {...rest}>
      {body}
    </button>
  );
}
