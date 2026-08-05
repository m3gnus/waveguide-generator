import { AppQueryProvider } from './queryClient';
import { Shell } from './shell/Shell';

export default function App() {
  return <AppQueryProvider><Shell /></AppQueryProvider>;
}
