import { Component, type ErrorInfo, type ReactNode } from 'react';
import { bundleUrl, reportClientError } from './api/diagnostics';
import { AppQueryProvider } from './queryClient';
import { Shell } from './shell/Shell';

/**
 * Last-resort boundary above the whole shell.
 *
 * The only other boundaries in the tree guard the WebGL canvas and the result
 * charts, so any other throw on the render path used to unmount the React
 * root: the window went blank with no message at all, and stayed blank across
 * relaunches when the throwing state had been persisted. This does not try to
 * recover -- it exists so a rendering failure is always *visible*, with the
 * message and a way to reload, instead of an empty window.
 */
export class AppErrorBoundary extends Component<{ children: ReactNode }, { message: string | null }> {
  state: { message: string | null } = { message: null };

  static getDerivedStateFromError(error: unknown): { message: string } {
    return {
      message: error instanceof Error && error.message
        ? error.message
        : 'The interface failed to render.',
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled render error', error, info.componentStack);
    // The console is not somewhere a user can be asked to look, and this
    // boundary is the last thing that runs before the window goes blank. One
    // POST puts the message where the next problem report will carry it.
    void reportClientError({
      message: error.message || 'The interface failed to render.',
      stack: [error.stack, info.componentStack].filter(Boolean).join('\n\n'),
      source: 'render',
    });
  }

  render(): ReactNode {
    if (this.state.message === null) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.75rem',
          height: '100vh',
          padding: '2rem',
          textAlign: 'center',
          fontFamily: 'system-ui, sans-serif',
          background: '#16181d',
          color: '#e6e8ec',
        }}
      >
        <h1 style={{ fontSize: '1.1rem', fontWeight: 600, margin: 0 }}>
          Waveguide Generator hit a rendering error
        </h1>
        <p style={{ margin: 0, maxWidth: '36rem', color: '#a9adb6' }}>{this.state.message}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{
            marginTop: '0.5rem',
            padding: '0.4rem 1.1rem',
            borderRadius: '4px',
            border: '1px solid #3a3f4a',
            background: '#242832',
            color: 'inherit',
            font: 'inherit',
            cursor: 'pointer',
          }}
        >
          Reload
        </button>
        {/* A plain anchor, deliberately. React is dead on this screen -- the
            tree that would have run a fetch and a blob download is the tree
            that just threw -- and the endpoint answers a GET with the file. */}
        <a
          href={bundleUrl()}
          download
          style={{ color: '#8ab4f8', fontSize: '0.85rem', marginTop: '0.25rem' }}
        >
          Download a problem report
        </a>
      </div>
    );
  }
}

export default function App() {
  return <AppErrorBoundary><AppQueryProvider><Shell /></AppQueryProvider></AppErrorBoundary>;
}
