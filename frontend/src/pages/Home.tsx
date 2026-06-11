import { Link } from "react-router-dom";
import { ArrowRight, Bot, BarChart3, Zap, UserCircle2 } from "lucide-react";
import { useTranslation } from "@/lib/i18n";

export function Home() {
  const { t } = useTranslation();
  const FEATURES = [
    { icon: Bot, title: t("home.features.agentTitle"), desc: t("home.features.agentDesc") },
    { icon: BarChart3, title: t("home.features.backtestTitle"), desc: t("home.features.backtestDesc") },
    { icon: Zap, title: t("home.features.streamingTitle"), desc: t("home.features.streamingDesc") },
    { icon: UserCircle2, title: t("home.features.replayTitle"), desc: t("home.features.replayDesc") },
  ];

  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-10 sm:p-8">
      <div className="max-w-2xl text-center space-y-5 sm:space-y-6">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">{t("home.title")}</h1>
        <p className="text-base text-muted-foreground sm:text-lg">{t("home.subtitle")}</p>
        <Link
          to="/agent"
          className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-primary text-primary-foreground font-medium hover:opacity-90 transition"
        >
          {t("home.startResearch")} <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      <div className="grid w-full max-w-5xl grid-cols-1 gap-4 mt-10 sm:gap-6 sm:mt-16 md:grid-cols-2 lg:grid-cols-4">
        {FEATURES.map(({ icon: Icon, title, desc }) => (
          <div key={title} className="border rounded-lg p-5 sm:p-6 space-y-3">
            <Icon className="h-8 w-8 text-primary" />
            <h3 className="font-semibold">{title}</h3>
            <p className="text-sm text-muted-foreground">{desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
