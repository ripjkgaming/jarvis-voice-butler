import { App } from '@/components/app/app';
import { getDefaultConfig } from '@/lib/utils';

export default function Page() {
  return <App appConfig={getDefaultConfig()} />;
}
