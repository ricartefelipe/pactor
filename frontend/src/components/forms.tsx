import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import type { FieldErrors } from "../api/errors";

interface FieldProps {
  label: string;
  name: string;
  error?: string | undefined;
  hint?: string | undefined;
}

function useFieldIds(error: string | undefined, hint: string | undefined) {
  const id = useId();
  const hintId = hint ? `${id}-dica` : undefined;
  const errorId = error ? `${id}-erro` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return { id, hintId, errorId, describedBy };
}

function FieldShell({
  id,
  label,
  required,
  hint,
  hintId,
  error,
  errorId,
  children,
}: {
  id: string;
  label: string;
  required: boolean;
  hint: string | undefined;
  hintId: string | undefined;
  error: string | undefined;
  errorId: string | undefined;
  children: ReactNode;
}) {
  return (
    <div className={error ? "field field--invalid" : "field"}>
      <label htmlFor={id}>
        {label}
        {required && <span aria-hidden="true"> *</span>}
      </label>
      {children}
      {hint && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className="field__error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}

export function TextField({
  label,
  name,
  error,
  hint,
  required = false,
  ...input
}: FieldProps & Omit<InputHTMLAttributes<HTMLInputElement>, "name" | "id">) {
  const ids = useFieldIds(error, hint);
  return (
    <FieldShell {...ids} label={label} required={required} hint={hint} error={error}>
      <input
        id={ids.id}
        name={name}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={ids.describedBy}
        {...input}
      />
    </FieldShell>
  );
}

export function TextAreaField({
  label,
  name,
  error,
  hint,
  required = false,
  ...textarea
}: FieldProps & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "name" | "id">) {
  const ids = useFieldIds(error, hint);
  return (
    <FieldShell {...ids} label={label} required={required} hint={hint} error={error}>
      <textarea
        id={ids.id}
        name={name}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={ids.describedBy}
        {...textarea}
      />
    </FieldShell>
  );
}

export interface Option {
  value: string;
  label: string;
}

export function SelectField({
  label,
  name,
  error,
  hint,
  options,
  placeholder,
  required = false,
  ...select
}: FieldProps & {
  options: readonly Option[];
  placeholder?: string;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "name" | "id">) {
  const ids = useFieldIds(error, hint);
  return (
    <FieldShell {...ids} label={label} required={required} hint={hint} error={error}>
      <select
        id={ids.id}
        name={name}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={ids.describedBy}
        {...select}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}

export function CheckboxField({
  label,
  name,
  hint,
  ...input
}: Omit<FieldProps, "error"> &
  Omit<InputHTMLAttributes<HTMLInputElement>, "name" | "id" | "type">) {
  const id = useId();
  const hintId = hint ? `${id}-dica` : undefined;
  return (
    <div className="field field--checkbox">
      <input id={id} type="checkbox" name={name} aria-describedby={hintId} {...input} />
      <label htmlFor={id}>{label}</label>
      {hint && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  );
}

/** Erro geral do formulário (o que não pertence a um campo específico). */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="form-error" role="alert">
      {message}
    </div>
  );
}

/** Valor textual de um campo do formulário ("" quando ausente ou arquivo). */
export function textOf(form: HTMLFormElement, name: string): string {
  const value = new FormData(form).get(name);
  return typeof value === "string" ? value : "";
}

/** Lê os campos de um formulário como texto; campos vazios ficam ausentes. */
export function formText(form: HTMLFormElement): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [name, value] of new FormData(form)) {
    if (typeof value === "string" && value.trim() !== "") values[name] = value.trim();
  }
  return values;
}

export function fieldError(errors: FieldErrors, name: string): string | undefined {
  return errors[name];
}

/** Várias escolhas com o mesmo nome (lidas com `FormData.getAll`). */
export function CheckboxGroup({
  legend,
  name,
  options,
  defaultValues = [],
  error,
}: {
  legend: string;
  name: string;
  options: readonly Option[];
  defaultValues?: readonly string[];
  error?: string | undefined;
}) {
  const id = useId();
  return (
    <fieldset
      className={error ? "field field--invalid checkbox-group" : "field checkbox-group"}
      aria-describedby={error ? `${id}-erro` : undefined}
    >
      <legend>{legend}</legend>
      {options.map((option) => (
        <div key={option.value} className="field--checkbox">
          <input
            id={`${id}-${option.value}`}
            type="checkbox"
            name={name}
            value={option.value}
            defaultChecked={defaultValues.includes(option.value)}
          />
          <label htmlFor={`${id}-${option.value}`}>{option.label}</label>
        </div>
      ))}
      {error && (
        <p className="field__error" id={`${id}-erro`}>
          {error}
        </p>
      )}
    </fieldset>
  );
}

export function checkedValues(form: HTMLFormElement, name: string): string[] {
  return new FormData(form)
    .getAll(name)
    .filter((value): value is string => typeof value === "string");
}
