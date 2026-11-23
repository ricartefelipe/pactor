import type { Schemas } from "../../api/client";

type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const DRAFTING: Readonly<Record<Schemas["DraftingStatusEnum"], Tone>> = {
  draft: "neutral",
  in_approval: "warning",
  approved: "info",
  signing: "warning",
  signed: "success",
  cancelled: "danger",
};

const LIFECYCLE: Readonly<Record<Schemas["LifecycleStatusEnum"], Tone>> = {
  not_started: "neutral",
  awaiting_start: "info",
  active: "success",
  suspended: "warning",
  ended: "neutral",
  terminated: "danger",
  archived: "neutral",
};

export function draftingTone(status: Schemas["DraftingStatusEnum"]): Tone {
  return DRAFTING[status];
}

export function lifecycleTone(status: Schemas["LifecycleStatusEnum"]): Tone {
  return LIFECYCLE[status];
}
