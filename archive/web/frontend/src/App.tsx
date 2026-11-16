import React from "react";
import { useStore } from "./store";
import Sidebar    from "./components/layout/Sidebar";
import TopBar     from "./components/layout/TopBar";
import Dashboard  from "./components/screens/Dashboard";
import Participant from "./components/screens/Participant";
import Record     from "./components/screens/Record";
import Review     from "./components/screens/Review";
import Analysis   from "./components/screens/Analysis";

export default function App() {
  const { screen } = useStore();

  const Screen = {
    dashboard:   Dashboard,
    participant: Participant,
    record:      Record,
    review:      Review,
    analysis:    Analysis,
  }[screen];

  return (
    <div className="h-screen flex overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto">
          <Screen />
        </main>
      </div>
    </div>
  );
}
