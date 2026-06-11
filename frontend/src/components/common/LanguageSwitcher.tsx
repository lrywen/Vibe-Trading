import { Languages } from "lucide-react";
import { useTranslation, type Language } from "@/lib/i18n";

export function LanguageSwitcher() {
  const { lang, setLanguage, t } = useTranslation();

  return (
    <label className="inline-flex items-center gap-2 rounded-md border bg-card/95 px-2 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur">
      <Languages className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="sr-only">{t("common.language")}</span>
      <select
        value={lang}
        onChange={(event) => setLanguage(event.target.value as Language)}
        className="bg-transparent text-xs font-medium text-foreground outline-none min-w-[86px]"
        aria-label={t("common.language")}
      >
        <option value="zh">{t("common.chinese")}</option>
        <option value="en">{t("common.english")}</option>
      </select>
    </label>
  );
}
