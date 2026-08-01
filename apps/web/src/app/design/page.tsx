import { DesignStudio } from "@/components/design-studio";
import { StudioGate } from "@/components/auth";

export default function DesignPage() {
  return (
    <div className="mx-auto w-full max-w-[1440px] px-4 py-6 sm:px-6 lg:px-10">
      <StudioGate>
        <DesignStudio />
      </StudioGate>
    </div>
  );
}
