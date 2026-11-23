import { useEffect, useId, useRef, type SubmitEvent, type ReactNode } from "react";

import { Button } from "./ui";

/**
 * Diálogo modal nativo (`<dialog>`): foco preso, Esc fecha e o foco volta ao
 * elemento que abriu o diálogo.
 */
export function Dialog({
  open,
  title,
  onClose,
  onSubmit,
  submitLabel,
  submitVariant = "primary",
  busy = false,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  onSubmit: (form: HTMLFormElement) => void;
  submitLabel: string;
  submitVariant?: "primary" | "danger";
  busy?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(event.currentTarget);
  }

  return (
    <dialog ref={ref} className="dialog" aria-labelledby={titleId} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <h2 id={titleId}>{title}</h2>
        <div className="dialog__body">{children}</div>
        <div className="dialog__actions">
          <Button variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" variant={submitVariant} busy={busy}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
