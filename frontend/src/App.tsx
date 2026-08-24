import { Component, type ErrorInfo, type ReactNode } from 'react';
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
      </div>
    );
  }
}

export default function App() {
  return <AppErrorBoundary><AppQueryProvider><Shell /></AppQueryProvider></AppErrorBoundary>;
}
