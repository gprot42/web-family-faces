import { useEffect, useRef } from "react";

export default function ConfirmAsk({
  title,
  body,
  children,
  confirmLabel = "Yes",
  cancelLabel = "No",
  danger = false,
  onConfirm,
  onCancel,
}) {
  const yes = useRef(null);
  useEffect(() => {
    yes.current?.focus();
    function onKey(event) {
      if (event.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="modal-backdrop" onClick={onCancel} role="presentation">
      <div
        className="modal scan-confirm"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-ask-title"
      >
        <div className="page-head">
          <div>
            <h2 id="confirm-ask-title">{title}</h2>
            {body ? <p className="hint">{body}</p> : null}
          </div>
        </div>
        {children}
        <div className="row scan-confirm-actions">
          <button type="button" className="ghost" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button type="button" ref={yes} className={danger ? "danger" : undefined} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
