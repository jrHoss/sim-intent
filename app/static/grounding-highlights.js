export function applyReturnedGrounding(grounding, applyHighlight) {
  applyHighlight({ reset: true });
  for (const result of grounding?.results || []) {
    if (result.clarification) {
      for (const candidate of result.clarification.candidate_sets) {
        applyHighlight({ entity_ids: candidate.entity_ids, style: "candidate" });
      }
      continue;
    }
    if (!result.region) continue;
    applyHighlight({ entity_ids: result.region.entity_ids, style: "proposed" });
    if (result.bc) {
      applyHighlight({
        entity_ids: result.region.entity_ids,
        style: "fixed_boundary_condition",
      });
    }
  }
}
