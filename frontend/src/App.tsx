import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Shell } from './shell/Shell';

const queryClient = new QueryClient();

export default function App() {
  return <QueryClientProvider client={queryClient}><Shell /></QueryClientProvider>;
}
