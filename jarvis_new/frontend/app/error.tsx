'use client';

import { FallbackSurface } from '@/components/app/fallback-surface';

/** Never display or log raw exception details, which may contain user data. */
export default function DisplayError() {
  return (
    <FallbackSurface
      code="DISPLAY"
      title="This view could not be displayed"
      description="The display encountered a problem while loading this view."
    />
  );
}
