import { ErrorBoundary } from "./components/ErrorBoundary";
import { ChatPage } from "./pages/ChatPage";
import { CoursePage } from "./pages/CoursePage";
import { HomePage } from "./pages/HomePage";
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
