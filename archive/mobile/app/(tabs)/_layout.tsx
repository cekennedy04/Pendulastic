/**
 * app/(tabs)/_layout.tsx
 * =======================
 * Light-theme bottom-tab navigator.
 */

import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";
import { Platform } from "react-native";
import { T, TYPE } from "../../constants/Theme";
import { useStore } from "../../store";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

const ICON_SIZE = 26;

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
          height:           Platform.OS === "ios" ? 88 : 68,
          paddingTop:       8,
        },
        tabBarActiveTintColor:   T.accent,
        tabBarInactiveTintColor: T.label,
        tabBarLabelStyle: { fontSize: TYPE.micro, fontWeight: "600", marginBottom: 2 },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Home",
          tabBarIcon: ({ color }) => (
            <Ionicons name="home-outline" size={ICON_SIZE} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="participant"
        options={{
          title: "Patient",
          tabBarIcon: ({ color }) => (
            <Ionicons name="person-outline" size={ICON_SIZE} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="record"
        options={{
          title: "Record",
          tabBarIcon: ({ color }) => (
            <Ionicons name="videocam-outline" size={ICON_SIZE} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="review"
        options={{
          title: "Review",
          tabBarIcon: ({ color }) => (
            <Ionicons name="checkmark-circle-outline" size={ICON_SIZE} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="analysis"
        options={{
          title: "Analysis",
          tabBarIcon: ({ color }) => (
            <Ionicons
              name="bar-chart-outline"
              size={ICON_SIZE}
              color={analysisUnlocked ? color : T.label}
            />
          ),
        }}
      />
    </Tabs>
  );
}
