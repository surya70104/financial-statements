-- ============================================================================
-- 015_step15_finalize_q1_q2_q4_q7.sql : the Product Owner's answers to the open questions
--   Q1  CHK-06 becomes a real check: 'Notes reconcile to statement face' (every note total = its Balance Sheet / P&L line, to the paisa).
--   Q2  The pilot is a Private Limited Company on template SCH3-COMPANY (Schedule III, Division I): confirmed. engagements gain an
--       entity_type ('private_limited' | 'partnership'): a partnership shows Partners' Capital / Current Account instead of Share
--       capital / Reserves and surplus. template_confirmed replaces the "not confirmed" banner.
--   Q7  The nine standard Schedule III / ICAI analytical ratios are confirmed (no longer provisional).
--   (Q4, the Word export, needs no schema change.)
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

ALTER TABLE engagements
  ADD COLUMN entity_type VARCHAR(32) NOT NULL DEFAULT 'private_limited' CHECK (entity_type IN ('private_limited', 'partnership')),
  ADD COLUMN template_confirmed BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE engagements SET template_confirmed = TRUE, template_confirmed_at = coalesce(template_confirmed_at, now()),
                       template_confirmed_by = coalesce(template_confirmed_by, 1);

UPDATE check_definitions
   SET name = 'Notes reconcile to statement face',
       description = 'Every note total equals the corresponding line on the face of the Balance Sheet / Statement of Profit and Loss, to the paisa (variance 0.00). Recomputed from the adjusted trial balance note by note.',
       rule_type = 'notes_reconcile_face', rule_config = '{}', definition_status = 'confirmed'
 WHERE code = 'CHK-06';

UPDATE ratio_definitions SET is_provisional = FALSE;
