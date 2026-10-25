import { useEffect, useId, useState, type ReactNode } from 'react';

interface CadDrawerProps {
  title: string;
  chip?: string;
  defaultOpen?: boolean;
  /** Opens the drawer whenever it turns true (something here needs the
   * user); the user can still fold it away again. */
  openWhen?: boolean;
  /** Keeps a closed drawer's body mounted, hidden: its contents keep reading
   * what they show (the solver frame Solve uses, say) while folded away. */
  keepMounted?: boolean;
  warning?: boolean;
  className?: string;
  children: ReactNode;
}

/** CAD sections use the same heading/button/chevron disclosure pattern as the
 * parameter rail, but keep their own compact card surface and state chip. */
export function CadDrawer({
  title, chip, defaultOpen = false, openWhen = false, keepMounted = false, warning = false, className = '', children,
}: CadDrawerProps) {
  const [open, setOpen] = useState(defaultOpen || openWhen);
  useEffect(() => { if (openWhen) setOpen(true); }, [openWhen]);
  const bodyId = useId();
  return <section className={`cad-section cad-drawer${open ? '' : ' closed'}${warning ? ' degraded' : ''}${className ? ` ${className}` : ''}`}>
    <h4 className="section-heading">
      <button type="button" className="section-head" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((value) => !value)}>
        <span className="chevron" aria-hidden="true">⌄</span>
        <span className="section-name">{title}</span>
        <span className="spacer"/>
        {chip && <span className={`cad-state-chip${warning ? ' warn' : ''}`}>{chip}</span>}
      </button>
    </h4>
    {(open || keepMounted) && <div id={bodyId} className="section-body" hidden={!open}>{children}</div>}
  </section>;
}
