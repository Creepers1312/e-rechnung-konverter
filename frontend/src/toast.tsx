import { useEffect, useState } from "react";

/** App-wide success message; survives page switches (e.g. after the last review item). */
const EVENT = "erechnung:toast";

export function showToast(message: string) {
  window.dispatchEvent(new CustomEvent(EVENT, { detail: message }));
}

export function Toaster() {
  const [message, setMessage] = useState("");
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const on = (e: Event) => {
      setMessage((e as CustomEvent<string>).detail);
      clearTimeout(timer);
      timer = setTimeout(() => setMessage(""), 4500);
    };
    window.addEventListener(EVENT, on);
    return () => {
      window.removeEventListener(EVENT, on);
      clearTimeout(timer);
    };
  }, []);
  return message ? (
    <div className="toast" role="status">
      ✓ {message}
    </div>
  ) : null;
}
