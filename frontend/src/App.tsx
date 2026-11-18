import { ErrorBoundary } from "./components/ErrorBoundary";
import { AdminPage } from "./pages/AdminPage";
import { ChatPage } from "./pages/ChatPage";
import { CoursePage } from "./pages/CoursePage";
import { HomePage } from "./pages/HomePage";
import { InvitePage } from "./pages/InvitePage";
import { TeamsConfigPage, TeamsTabPage } from "./pages/TeamsPage";
import { LtiPage } from "./pages/LtiPage";
import { TeacherHome } from "./pages/TeacherHome";
import { type Route, useRoute } from "./router";

function Page({ route }: { route: Route }) {
  switch (route.page) {
    case "teacher":
      return <TeacherHome />;
    case "course":
      return <CoursePage kbId={route.kbId} />;
    case "chat":
      return <ChatPage kbId={route.kbId} />;
    case "lti":
      return <LtiPage query={route.query} />;
    case "admin":
      return <AdminPage />;
    case "teams":
      return route.config ? <TeamsConfigPage /> : <TeamsTabPage />;
    case "invite":
      return <InvitePage code={route.code} />;
    default:
      return <HomePage />;
  }
}

export function App() {
  const route = useRoute();
  // Keyed by the route, so moving to another page clears an earlier error.
  return (
    <ErrorBoundary key={window.location.hash}>
      <Page route={route} />
    </ErrorBoundary>
  );
}
