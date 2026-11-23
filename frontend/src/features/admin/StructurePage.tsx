import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SubmitEvent } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useDepartments, useInvalidate, useOrgKey, useTeams } from "../../app/queries";
import { useSession } from "../../auth/session";
import {
  CheckboxField,
  FormError,
  TextAreaField,
  TextField,
  formText,
} from "../../components/forms";
import { useNotify } from "../../components/notifications";
import { Restricted } from "../../components/Restricted";
import { EmptyState, QueryView } from "../../components/states";
import { Badge, Button, PageHeader, Section } from "../../components/ui";
import { formatDate } from "../../lib/format";

function NamedList({
  title,
  query,
  create,
}: {
  title: string;
  query: ReturnType<typeof useDepartments>;
  create: (name: string) => Promise<unknown>;
}) {
  const notify = useNotify();
  const mutation = useMutation({
    mutationFn: create,
    onSuccess: () => {
      notify.success(`${title}: item criado.`);
    },
  });
  return (
    <Section title={title}>
      <QueryView query={query}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="Nenhum cadastro." />
          ) : (
            <ul className="tag-list">
              {items.map((item) => (
                <li key={item.id}>{item.name}</li>
              ))}
            </ul>
          )
        }
      </QueryView>
      <form
        className="form form--inline"
        noValidate
        onSubmit={(event: SubmitEvent<HTMLFormElement>) => {
          event.preventDefault();
          const form = event.currentTarget;
          mutation.mutate(formText(form).name ?? "", {
            onSuccess: () => {
              form.reset();
            },
          });
        }}
      >
        <FormError message={mutation.isError ? errorMessage(mutation.error) : null} />
        <TextField label="Nome" name="name" required maxLength={120} />
        <Button type="submit" busy={mutation.isPending}>
          Adicionar
        </Button>
      </form>
    </Section>
  );
}

/** Linhas "AAAA-MM-DD Nome do feriado". */
function parseHolidays(text: string): { holidays: Schemas["HolidayRequest"][]; invalid: string[] } {
  const holidays: Schemas["HolidayRequest"][] = [];
  const invalid: string[] = [];
  for (const line of text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)) {
    const match = /^(\d{4}-\d{2}-\d{2})\s+(.+)$/.exec(line);
    if (match?.[1] && match[2]) holidays.push({ day: match[1], name: match[2] });
    else invalid.push(line);
  }
  return { holidays, invalid };
}

function HolidayCalendars() {
  const key = useOrgKey();
  const { can } = useSession();
  const notify = useNotify();
  const invalidate = useInvalidate();
  const [invalidLines, setInvalidLines] = useState<string[]>([]);
  const calendars = useQuery({
    queryKey: key("holiday-calendars"),
    queryFn: () => unwrap(api.GET("/api/v1/holiday-calendars")),
  });
  const create = useMutation({
    mutationFn: (body: Schemas["HolidayCalendarCreateRequest"]) =>
      unwrap(api.POST("/api/v1/holiday-calendars", { body })),
    onSuccess: async () => {
      notify.success("Calendário de feriados criado.");
      await invalidate("holiday-calendars");
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {};

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formText(form);
    const { holidays, invalid } = parseHolidays(values.holidays ?? "");
    setInvalidLines(invalid);
    if (invalid.length > 0) return;
    create.mutate({
      name: values.name ?? "",
      is_default: new FormData(form).get("is_default") === "on",
      holidays,
    });
  }

  return (
    <Section title="Calendários de feriados">
      <p className="muted">
        O Pactor não embute listas oficiais de feriados: cada organização informa os seus, que valem
        para prazos em dias úteis.
      </p>
      <QueryView query={calendars}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="Nenhum calendário." />
          ) : (
            items.map((calendar) => (
              <details key={calendar.id} className="card">
                <summary>
                  {calendar.name} {calendar.is_default && <Badge tone="info">Padrão</Badge>} ·{" "}
                  {calendar.holidays.length} feriados
                </summary>
                <ul>
                  {calendar.holidays.map((holiday) => (
                    <li key={holiday.day}>
                      {formatDate(holiday.day)} — {holiday.name}
                    </li>
                  ))}
                </ul>
              </details>
            ))
          )
        }
      </QueryView>
      {can("obligations.manage") && (
        <form className="form" onSubmit={handleSubmit} noValidate>
          <h3>Novo calendário</h3>
          <FormError message={create.isError ? errorMessage(create.error) : null} />
          <TextField label="Nome" name="name" required error={errors.name} />
          <TextAreaField
            label="Feriados"
            name="holidays"
            rows={6}
            required
            hint="Um por linha: AAAA-MM-DD Nome. Ex.: 2027-12-25 Natal"
            error={
              invalidLines.length > 0
                ? `Linhas inválidas: ${invalidLines.join("; ")}`
                : errors.holidays
            }
          />
          <CheckboxField label="Usar como calendário padrão" name="is_default" />
          <Button type="submit" busy={create.isPending}>
            Criar calendário
          </Button>
        </form>
      )}
    </Section>
  );
}

export function StructurePage() {
  const departments = useDepartments();
  const teams = useTeams();
  const invalidate = useInvalidate();
  return (
    <Restricted permission="members.manage">
      <PageHeader title="Departamentos e equipes" />
      <div className="columns">
        <NamedList
          title="Departamentos"
          query={departments}
          create={async (name) => {
            await unwrap(api.POST("/api/v1/departments", { body: { name } }));
            await invalidate("departments");
          }}
        />
        <NamedList
          title="Equipes"
          query={teams}
          create={async (name) => {
            await unwrap(api.POST("/api/v1/teams", { body: { name } }));
            await invalidate("teams");
          }}
        />
      </div>
      <HolidayCalendars />
    </Restricted>
  );
}
