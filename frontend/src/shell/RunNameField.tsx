import { nextRunLabel } from '../jobs/runNaming';
import { useRunNameSource } from '../jobs/runNameSource';
import { usePreferences } from '../prefs/preferences';
import { useDesignNameDraft } from '../design/designNameDraft';

/**
 * The design's name, and the label the next solve will be stored under.
 *
 * This is the one editable name in WG. It renames the document, the file it
 * saves as, the `Report.Title` inside that file, and every run and export made
 * from it -- which is what stopped the run list, the file chip, and the `.cfg`
 * from each answering to a different name.
 */
export function RunNameField({ now = new Date() }: { now?: Date }) {
  const preferences = usePreferences();
  const { name } = useRunNameSource();
  const displayedLabel = nextRunLabel(name, preferences, now);
  const draft = useDesignNameDraft(name);
  return <div className="run-name-field">
    <label className="ui-field">Design name<input
      aria-label="Design name"
      placeholder="Untitled"
      title="Names the design, its design file, and every run and export made from it"
      {...draft}
    /></label>
    <span className="run-name-preview" title="The name the next solve is stored under">next · <b>{displayedLabel}</b></span>
  </div>;
}
