import { useEffect, useState } from 'react';
import { DesignFileMenu } from '../design/DesignFileMenu';
import { useDesignStore } from '../stores/design';
import { BrandMark, Icon } from './icons';
import { useSolveControl } from './JobsCoordinator';

type Theme = 'dark' | 'light';
const THEME_KEY = 'wg2.theme';

function initialTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  return saved === 'light' ? 'light' : 'dark';
}

export function TopBar({ onResetLayout }: { onResetLayout: () => void }) {
  const solve = useSolveControl();
  const undo = useDesignStore((state) => state.undo);
  const redo = useDesignStore((state) => state.redo);
  const revision = useDesignStore((state) => state.designRevision);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const temporal = useDesignStore.temporal.getState();
  const canUndo = temporal.pastStates.length > 0 || Boolean(useDesignStore.getState().dragSnapshot);
  const canRedo = temporal.futureStates.length > 0;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  return <header className="topbar">
    <div className="brand"><BrandMark/><div><span className="brand-name">WAVEGUIDE GENERATOR</span><span className="brand-version">{__WG2_VERSION__} · local</span></div></div>
    <i className="v-separator" />
    <DesignFileMenu />
    <div className="button-group">
      <button className="icon-button" disabled={!canUndo} onClick={undo} title="Undo"><Icon name="undo"/></button>
      <button className="icon-button" disabled={!canRedo} onClick={redo} title="Redo"><Icon name="redo"/></button>
    </div>
    <button className="command-affordance"><Icon name="search"/><span>Search parameters, designs, jobs, commands…</span><kbd>⌘K</kbd></button>
    <button className="solve-button" disabled={solve.disabled} title={solve.title} aria-busy={solve.submitting} onClick={solve.solve}><Icon name="play"/>Solve<kbd>⌘↵</kbd></button>
    <i className="v-separator" />
    <div className="theme-toggle" aria-label="Color theme">
      <button className={theme === 'dark' ? 'on' : ''} onClick={() => setTheme('dark')} aria-label="Dark theme"><Icon name="moon"/></button>
      <button className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')} aria-label="Light theme"><Icon name="sun"/></button>
    </div>
    <button className="icon-button" onClick={onResetLayout} title="Reset layout"><Icon name="layout"/></button>
    <span className="revision-chip" title="Design revision">r{revision}</span>
  </header>;
}
