/**
 * app/(tabs)/_layout.tsx
 * =======================
 * Light-theme bottom-tab navigator.
 */

import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";
import { T } from "../../constants/Theme";
import { useStore } from "../../store";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

export default function TabLayout() {
  const analysisUnlocked = useStore((s) => s.analysisUnlocked);

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor:  T.card,
          borderTopColor:   T.border,
          borderTopWidth:   1,
        },
        tabBarActiveTintColor:   T.accent,
        tabBarInactiveTintColor: T.label,
        tabBarLabelStyle: { fontSize: 11, marginBottom: 2 },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Home",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="home-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="participant"
        options={{
          title: "Patient",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="person-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="record"
        options={{
          title: "Record",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="videocam-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="review"
        options={{
          title: "Review",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="checkmark-circle-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="analysis"
        options={{
          title: "Analysis",
          tabBarIcon: ({ color, size }) => (
            <Ionicons
              name="bar-chart-outline"
              size={size}
              color={analysisUnlocked ? color : T.label}
            />
          ),
        }}
      />
    </Tabs>
  );
}
