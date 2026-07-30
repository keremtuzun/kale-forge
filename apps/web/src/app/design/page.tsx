import { DesignStudio } from "@/components/design-studio";
import { StudioGate } from "@/components/auth";

export default function DesignPage() {
  return (
    <div className="mx-auto w-full max-w-7xl px-6 py-8">
      <StudioGate>
        <DesignStudio />
      </StudioGate>
    </div>
  );
}
