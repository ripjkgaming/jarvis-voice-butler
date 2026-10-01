import { FallbackSurface } from '@/components/app/fallback-surface';

export default function NotFound() {
  return (
    <FallbackSurface
      code="404"
      title="View not found"
      description="This address does not match a Jarvis view."
    />
  );
}
