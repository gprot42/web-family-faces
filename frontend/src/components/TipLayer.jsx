import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

const PAD = 8;

function tipTarget(node) {
  if (!node || !node.closest) return null;
  return node.closest("[data-tip]");
}

export default function TipLayer() {
  const loc = useLocation();
  const [tip, setTip] = useState(null);
  const [box, setBox] = useState({ left: 0, top: 0, ready: false });
  const bubble = useRef(null);

  useEffect(() => {
    setTip(null);
  }, [loc.pathname, loc.search, loc.hash]);

  useEffect(() => {
    function show(target) {
      const text = target?.getAttribute("data-tip");
      if (!text) {
        setTip(null);
        return;
      }
      setTip({
        text,
        r: target.getBoundingClientRect(),
        side: target.closest(".nav") ? "right" : "above",
      });
    }
    function onOver(event) {
      const target = tipTarget(event.target);
      if (target) show(target);
    }
    function onOut(event) {
      const target = tipTarget(event.target);
      if (!target) return;
      const next = event.relatedTarget;
      if (next && target.contains(next)) return;
      setTip(null);
    }
    function hide() {
      setTip(null);
    }
    document.addEventListener("pointerover", onOver);
    document.addEventListener("pointerout", onOut);
    document.addEventListener("pointerdown", hide);
    document.addEventListener("focusin", onOver);
    document.addEventListener("focusout", onOut);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      document.removeEventListener("pointerover", onOver);
      document.removeEventListener("pointerout", onOut);
      document.removeEventListener("pointerdown", hide);
      document.removeEventListener("focusin", onOver);
      document.removeEventListener("focusout", onOut);
      document.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, []);

  useLayoutEffect(() => {
    if (!tip || !bubble.current) {
      setBox({ left: 0, top: 0, ready: false });
      return;
    }
    const size = bubble.current.getBoundingClientRect();
    const maxLeft = Math.max(PAD, window.innerWidth - size.width - PAD);
    const maxTop = Math.max(PAD, window.innerHeight - size.height - PAD);
    let left;
    let top;
    if (tip.side === "right") {
      left = tip.r.right + 10;
      top = tip.r.top + tip.r.height / 2 - size.height / 2;
      if (left + size.width > window.innerWidth - PAD) {
        left = tip.r.left - size.width - 10;
      }
    } else {
      left = tip.r.left + tip.r.width / 2 - size.width / 2;
      top = tip.r.top - size.height - 8;
      if (top < PAD) top = tip.r.bottom + 8;
    }
    setBox({
      left: Math.min(maxLeft, Math.max(PAD, left)),
      top: Math.min(maxTop, Math.max(PAD, top)),
      ready: true,
    });
  }, [tip]);

  if (!tip) return null;
  return (
    <div
      ref={bubble}
      className="tip-bubble"
      role="tooltip"
      style={{
        left: box.left,
        top: box.top,
        visibility: box.ready ? "visible" : "hidden",
        maxWidth: Math.min(280, window.innerWidth - PAD * 2),
      }}
    >
      {tip.text}
    </div>
  );
}