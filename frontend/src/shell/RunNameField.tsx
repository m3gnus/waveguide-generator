import type { ReactNode } from 'react';
import { nextRunLabel, normalizeRunName, runSequenceFor } from '../jobs/runNaming';
import { useRunNameSource } from '../jobs/runNameSource';
import { preferencesStore, RUN_SEQUENCE_RANGE, usePreferences } from '../prefs/preferences';
import { useDesignNameDraft } from '../design/designNameDraft';

/**
 * The design's name, and the label the next solve will be stored under.
 *
 * This is the one editable name in WG. It renames the document, the file it
 * saves as, the `Report.Title` inside that file, and every run and export made
 * from it -- which is what stopped the run list, the file chip, and the `.cfg`
 * from each answering to a different name.
 *
 * It heads the Jobs panel, beside the runs it names; `children` is the Job
 * preferences button, which sets how the next name is built.
 */
export function RunNameField({ now = new Date(), children }: { now?: Date; children?: ReactNode }) {
  const preferences = usePreferences();
  const { name } = useRunNameSource();
  const displayedLabel = nextRunLabel(name, preferences, now);
  const draft = useDesignNameDraft(name);
  const numbered = preferences.runNameNumberPosition !== 'off';
  const sequence = runSequenceFor(preferences, name);
  // The counter belongs to the design name on screen: storing the name with the
  // number keeps runSequenceFor from resetting it to 1 for this design. The
  // preference store owns the range and rounding.
  const setNextNumber = (value: number) => {
    if (!Number.isFinite(value)) return;
    preferencesStore.update({ runSequenceName: normalizeRunName(name), runSequenceNext: value });
  };
  return <div className="run-name-field">
    <div className="run-name-row">
      <label className="ui-field">Design name<input
        aria-label="Design name"
        placeholder="Untitled"
        title="Names the design, its design file, and every run and export made from it"
        {...draft}
      /></label>
      {children}
    </div>
    <div className="run-name-next">
      <span className="run-name-preview" title="The name the next solve is stored under">Next · <b>{displayedLabel}</b></span>
      {numbered && <label className="run-next-number" title="The number the next run of this design takes">No.<input
        aria-label="Next run number"
        type="number"
        min={RUN_SEQUENCE_RANGE[0]}
        max={RUN_SEQUENCE_RANGE[1]}
        step={1}
        value={sequence}
        onChange={(event) => setNextNumber(event.target.valueAsNumber)}
      /></label>}
    </div>
  </div>;
}
