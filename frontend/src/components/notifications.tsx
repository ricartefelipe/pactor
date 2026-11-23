import { createContext, use, useCallback, useMemo, useState, type ReactNode } from "react";

interface Notice {
  id: number;
  tone: "success" | "error";
  message: string;
}

interface Notifier {
  success: (message: string) => void;
  error: (message: string) => void;
}

const NotifierContext = createContext<Notifier | null>(null);
const DISMISS_AFTER_MS = 6000;

/** Mensagens de resultado anunciadas por leitores de tela (região aria-live). */
export function NotificationsProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);

  const dismiss = useCallback((id: number) => {
    setNotices((current) => current.filter((notice) => notice.id !== id));
  }, []);

  const push = useCallback(
    (tone: Notice["tone"], message: string) => {
      const id = Date.now() + Math.random();
      setNotices((current) => [...current, { id, tone, message }]);
      window.setTimeout(() => {
        dismiss(id);
      }, DISMISS_AFTER_MS);
    },
    [dismiss],
  );

  const notifier = useMemo<Notifier>(
    () => ({
      success: (message) => {
        push("success", message);
      },
      error: (message) => {
        push("error", message);
      },
    }),
    [push],
  );

  return (
    <NotifierContext value={notifier}>
      {children}
      <div className="notices" aria-live="polite" aria-atomic="false">
        {notices.map((notice) => (
          <div
            key={notice.id}
            className={`notice notice--${notice.tone}`}
            role={notice.tone === "error" ? "alert" : "status"}
          >
            <span>{notice.message}</span>
            <button
              type="button"
              className="notice__close"
              aria-label="Fechar aviso"
              onClick={() => {
                dismiss(notice.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </NotifierContext>
  );
}

export function useNotify(): Notifier {
  const notifier = use(NotifierContext);
  if (notifier === null) throw new Error("useNotify fora de NotificationsProvider");
  return notifier;
}
