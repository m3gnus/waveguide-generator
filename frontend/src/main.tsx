import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { restoreAutosave, startAutosave } from './stores/autosave';
import './styles/app.css';

restoreAutosave();
const autosave = startAutosave();
if (import.meta.hot) import.meta.hot.dispose(() => autosave.dispose());

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
