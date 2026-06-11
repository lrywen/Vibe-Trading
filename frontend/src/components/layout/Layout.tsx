import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { BarChart3, Bot, Moon, Sun, Plus, Trash2, Pencil, MessageSquare, ChevronsLeft, ChevronsRight, Settings, Layers, Loader2, Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type SessionItem } from "@/lib/api";
import { useAgentStore } from "@/stores/agent";
import { ConnectionBanner } from "@/components/layout/ConnectionBanner";
import { LanguageSwitcher } from "@/components/common/LanguageSwitcher";
import { useTranslation } from "@/lib/i18n";


// Bump on each release; one place keeps the footer in sync with package.json.
const APP_VERSION = "v0.1.9";

const NAV = [
  { to: "/", icon: BarChart3, labelKey: "nav.home" },
  { to: "/agent", icon: Bot, labelKey: "nav.agent" },
  { to: "/alpha-zoo", icon: Layers, labelKey: "nav.alphaZoo" },
  { to: "/settings", icon: Settings, labelKey: "nav.settings" },
  { to: "/correlation", icon: BarChart3, labelKey: "nav.correlation" },
];

export function Layout() {
  const { pathname } = useLocation();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();
  const { dark, toggle } = useDarkMode();
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const sseStatus = useAgentStore(s => s.sseStatus);
  const sseRetryAttempt = useAgentStore(s => s.sseRetryAttempt);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("qa-sidebar") === "collapsed");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  

  const activeSessionId = searchParams.get("session");
  const streamingSessionId = useAgentStore(s => s.streamingSessionId);

  useEffect(() => {
    localStorage.setItem("qa-sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);

  const loadSessions = () => {
    api.listSessions()
      .then((list) => setSessions(Array.isArray(list) ? list : []))
      .catch(() => {})
      .finally(() => setSessionsLoading(false));
  };

  // Load sessions on mount. Also refresh when navigating TO /agent or when
  // the active session changes (covers new session creation from Agent).
  const isAgentPage = pathname.startsWith("/agent");
  useEffect(() => { loadSessions(); }, [isAgentPage, activeSessionId]);
  useEffect(() => { setMobileNavOpen(false); }, [pathname, activeSessionId]);

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const deleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      setSessions((prev) => prev.filter((s) => s.session_id !== sid));
    } catch { /* ignore */ }
    setDeleteTarget(null);
  };

  const renameSession = async (sid: string) => {
    if (!renameValue.trim()) { setRenameTarget(null); return; }
    try {
      await api.renameSession(sid, renameValue.trim());
      setSessions((prev) => prev.map((s) => s.session_id === sid ? { ...s, title: renameValue.trim() } : s));
    } catch { /* ignore */ }
    setRenameTarget(null);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <header className="fixed inset-x-0 top-0 z-[9997] flex h-14 items-center justify-between border-b bg-card/95 px-3 backdrop-blur md:hidden">
        <button
          onClick={() => setMobileNavOpen(true)}
          className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label={t("nav.more")}
        >
          <Menu className="h-5 w-5" />
        </button>
        <Link to="/" className="flex min-w-0 items-center gap-2 font-bold tracking-tight">
          <BarChart3 className="h-5 w-5 shrink-0 text-primary" />
          <span className="truncate">Vibe-Trading</span>
        </Link>
        <div className="flex items-center gap-1">
          <button
            onClick={toggle}
            className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={dark ? t("nav.lightMode") : t("nav.darkMode")}
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <LanguageSwitcher />
        </div>
      </header>

      {/* Sidebar */}
      <aside className={cn(
        "hidden border-r bg-card flex-col shrink-0 transition-all duration-200 md:flex",
        collapsed ? "w-12" : "w-64"
      )}>
        {/* Brand */}
        <div className={cn("border-b", collapsed ? "p-2 flex justify-center" : "p-4")}>
          <Link to="/" className={cn("flex items-center font-bold text-base tracking-tight", collapsed ? "justify-center" : "gap-2")}>
            <BarChart3 className="h-5 w-5 text-primary shrink-0" />
            {!collapsed && "Vibe-Trading"}
          </Link>
        </div>

        {/* Nav */}
        <nav className={cn("space-y-0.5", collapsed ? "p-1" : "p-2")}>
          {NAV.map(({ to, icon: Icon, labelKey }) => {
            const text = t(labelKey);
            return (
              <Link
                key={to}
                to={to}
                className={cn(
                  "flex items-center rounded-md text-sm transition-colors",
                  collapsed ? "justify-center p-2" : "gap-3 px-3 py-2",
                  (to === "/" ? pathname === "/" : pathname.startsWith(to))
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
                title={collapsed ? text : undefined}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                {!collapsed && text}
              </Link>
            );
          })}
        </nav>

        {/* Sessions — hidden when collapsed */}
        {!collapsed && (
          <div className="flex-1 overflow-auto border-t mt-2 flex flex-col">
            <div className="flex items-center justify-between px-4 py-2">
              <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <MessageSquare className="h-3.5 w-3.5" />
                {t("nav.conversationHistory")}
              </span>
              <Link
                to="/agent"
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                title={t("nav.newConversation")}
              >
                <Plus className="h-3.5 w-3.5" />
              </Link>
            </div>

            <div className="px-2 pb-2 space-y-0.5 overflow-auto flex-1">
              {sessionsLoading ? (
                <div className="space-y-1.5 px-2 py-1">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-7 rounded-md bg-muted/50 animate-pulse" />
                  ))}
                </div>
              ) : sessions.length === 0 ? (
                <p className="px-3 py-2 text-xs text-muted-foreground/60">{t("nav.noConversations")}</p>
              ) : null}
              {sessions.map((s) => {
                const isActive = s.session_id === activeSessionId;
                const isDeleting = deleteTarget === s.session_id;
                const isRenaming = renameTarget === s.session_id;
                return (
                  <div key={s.session_id} className="group relative flex items-center">
                    {isRenaming ? (
                      <input
                        autoFocus
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") renameSession(s.session_id); if (e.key === "Escape") setRenameTarget(null); }}
                        onBlur={() => renameSession(s.session_id)}
                        className="flex-1 min-w-0 pl-3 pr-2 py-1 rounded-md text-xs border border-primary bg-background outline-none"
                      />
                    ) : (
                      <Link
                        to={`/agent?session=${s.session_id}`}
                        className={cn(
                          "flex-1 min-w-0 pl-3 pr-14 py-1.5 rounded-md text-xs transition-colors truncate block border-l-2",
                          isActive
                            ? "border-l-primary bg-primary/10 text-primary font-medium"
                            : "border-l-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
                        )}
                        title={s.title || s.session_id}
                      >
                        <span className="flex items-center gap-1.5">
                          {streamingSessionId === s.session_id ? (
                            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
                          ) : (
                            <span className={cn(
                              "h-1.5 w-1.5 rounded-full shrink-0",
                              isActive ? "bg-primary/70" : "bg-muted-foreground/40"
                            )} />
                          )}
                          {s.title || s.session_id.slice(0, 16)}
                        </span>
                      </Link>
                    )}
                    {!isRenaming && isDeleting ? (
                      <div className="absolute right-0.5 flex items-center gap-0.5">
                        <button onClick={() => deleteSession(s.session_id)} className="p-1 text-danger hover:bg-danger/10 rounded text-[10px] font-medium">{t("common.yes")}</button>
                        <button onClick={() => setDeleteTarget(null)} className="p-1 text-muted-foreground hover:bg-muted rounded text-[10px]">{t("common.no")}</button>
                      </div>
                    ) : !isRenaming ? (
                      <div className="absolute right-1 opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-opacity">
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setRenameTarget(s.session_id); setRenameValue(s.title || ""); }}
                          className="p-1 text-muted-foreground hover:text-foreground rounded"
                          title={t("common.edit")}
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setDeleteTarget(s.session_id); }}
                          className="p-1 text-muted-foreground hover:text-danger rounded"
                          title={t("common.delete")}
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Spacer when collapsed */}
        {collapsed && <div className="flex-1" />}

        {/* Footer */}
        <div className={cn("border-t", collapsed ? "p-1 flex flex-col items-center gap-1" : "p-3 space-y-2")}>
          {collapsed ? (
            <>
              <button onClick={toggle} className="p-1.5 text-muted-foreground hover:text-foreground rounded transition-colors" title={dark ? t("nav.lightMode") : t("nav.darkMode")}>
                {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
              </button>
              <button onClick={() => setCollapsed(false)} className="p-1.5 text-muted-foreground hover:text-foreground rounded transition-colors" title={t("nav.more")}>
                <ChevronsRight className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <button
                  onClick={toggle}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
                  {dark ? t("nav.lightMode") : t("nav.darkMode")}
                </button>
                <button
                  onClick={() => setCollapsed(true)}
                  className="p-1 text-muted-foreground hover:text-foreground rounded transition-colors"
                  title={t("nav.less")}
                >
                  <ChevronsLeft className="h-3.5 w-3.5" />
                </button>
              </div>
              <p className="text-xs text-muted-foreground/60">{APP_VERSION}</p>
            </>
          )}
        </div>
      </aside>

      {mobileNavOpen && (
        <div className="fixed inset-0 z-[9998] md:hidden">
          <button
            className="absolute inset-0 bg-background/70 backdrop-blur-sm"
            onClick={() => setMobileNavOpen(false)}
            aria-label={t("common.cancel")}
          />
          <aside className="relative z-10 flex h-full w-[min(18rem,85vw)] flex-col border-r bg-card shadow-xl">
            <div className="flex items-center justify-between border-b p-4">
              <Link to="/" className="flex min-w-0 items-center gap-2 font-bold tracking-tight">
                <BarChart3 className="h-5 w-5 shrink-0 text-primary" />
                <span className="truncate">Vibe-Trading</span>
              </Link>
              <button
                onClick={() => setMobileNavOpen(false)}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={t("common.cancel")}
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <nav className="space-y-1 p-3">
              {NAV.map(({ to, icon: Icon, labelKey }) => {
                const text = t(labelKey);
                return (
                  <Link
                    key={to}
                    to={to}
                    className={cn(
                      "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm transition-colors",
                      (to === "/" ? pathname === "/" : pathname.startsWith(to))
                        ? "bg-primary/10 text-primary font-medium"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                    {text}
                  </Link>
                );
              })}
            </nav>

            <div className="mt-2 flex min-h-0 flex-1 flex-col border-t">
              <div className="flex items-center justify-between px-4 py-2">
                <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <MessageSquare className="h-3.5 w-3.5" />
                  {t("nav.conversationHistory")}
                </span>
                <Link
                  to="/agent"
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  title={t("nav.newConversation")}
                >
                  <Plus className="h-3.5 w-3.5" />
                </Link>
              </div>
              <div className="flex-1 space-y-0.5 overflow-auto px-2 pb-3">
                {sessionsLoading ? (
                  <div className="space-y-1.5 px-2 py-1">
                    {[1, 2, 3].map((i) => (
                      <div key={i} className="h-8 rounded-md bg-muted/50 animate-pulse" />
                    ))}
                  </div>
                ) : sessions.length === 0 ? (
                  <p className="px-3 py-2 text-xs text-muted-foreground/60">{t("nav.noConversations")}</p>
                ) : null}
                {sessions.map((s) => {
                  const isActive = s.session_id === activeSessionId;
                  return (
                    <Link
                      key={s.session_id}
                      to={`/agent?session=${s.session_id}`}
                      className={cn(
                        "flex min-w-0 items-center gap-1.5 truncate rounded-md border-l-2 px-3 py-2 text-xs transition-colors",
                        isActive
                          ? "border-l-primary bg-primary/10 text-primary font-medium"
                          : "border-l-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
                      )}
                      title={s.title || s.session_id}
                    >
                      {streamingSessionId === s.session_id ? (
                        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
                      ) : (
                        <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", isActive ? "bg-primary/70" : "bg-muted-foreground/40")} />
                      )}
                      <span className="truncate">{s.title || s.session_id.slice(0, 16)}</span>
                    </Link>
                  );
                })}
              </div>
            </div>

            <div className="border-t p-3 text-xs text-muted-foreground/60">{APP_VERSION}</div>
          </aside>
        </div>
      )}

      {/* Main */}
      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden pt-14 md:pt-0">
        <ConnectionBanner status={sseStatus} retryAttempt={sseRetryAttempt} />
        <div className="fixed right-4 top-3 z-[9999] hidden md:block">
          <LanguageSwitcher />
        </div>
        <main className="flex-1 overflow-auto md:pt-12">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
