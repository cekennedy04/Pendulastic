/**
 * components/camera/SkeletonOverlay.tsx
 * ======================================
 * Renders a three-joint skeleton (hip → knee → ankle) as an SVG overlay
 * positioned absolutely above the camera viewfinder.
 *
 * Keypoint coordinates arrive as normalised [0, 1] values from the backend.
 * The overlay maps them to pixel positions using the supplied `width` and
 * `height` of the viewfinder container.
 *
 * Animation strategy
 * ------------------
 * React Native's built-in `Animated` library drives joint positions through
 * spring physics.  Each joint owns a pair of `Animated.Value` instances
 * (x, y) initialised to the viewport centre.  When the backend delivers new
 * keypoints, `Animated.parallel` starts a spring for every axis simultaneously
 * so all joints move together.  `useNativeDriver: false` is required because
 * react-native-svg props are not backed by native animated nodes.
 *
 * Pulse animation
 * ---------------
 * When `trackingStatus === "uncertain"`, an `AnimatedG` wrapper loops a slow
 * opacity pulse (1.0 → 0.35 → 1.0, 550 ms each half-cycle) so the clinician
 * notices the reduced confidence without losing sight of the joints.
 */

import React, { useEffect, useRef } from "react";
import { Animated } from "react-native";
import Svg, { Circle, G, Line, Text } from "react-native-svg";
import type { PoseKeypoints, TrackingStatus } from "../../types";

// Cast to any: react-native-svg types don't declare Animated.Value as a valid
// prop type, but createAnimatedComponent resolves it correctly at runtime.
const AnimatedCircle = Animated.createAnimatedComponent(Circle) as React.ComponentType<any>;
const AnimatedLine   = Animated.createAnimatedComponent(Line)   as React.ComponentType<any>;
const AnimatedText   = Animated.createAnimatedComponent(Text)   as React.ComponentType<any>;
const AnimatedG      = Animated.createAnimatedComponent(G)      as React.ComponentType<any>;

const SPRING = {
  damping:         18,
  stiffness:       200,
  mass:            0.4,
  useNativeDriver: false,
} as const;

const C_BONE  = "#0ea5e9";
const C_HIP   = "#f97316";
const C_KNEE  = "#0ea5e9";
const C_ANKLE = "#22c55e";
const C_ANGLE = "#facc15";

interface Props {
  width:          number;
  height:         number;
  keypoints:      PoseKeypoints | null;
  kneeAngleDeg:   number;
  trackingStatus: TrackingStatus;
}

export const SkeletonOverlay = React.memo(function SkeletonOverlay({
  width,
  height,
  keypoints,
  kneeAngleDeg,
  trackingStatus,
}: Props) {
  // One Animated.Value per joint axis — created once, reused across renders.
  const hipX   = useRef(new Animated.Value(width  * 0.5 )).current;
  const hipY   = useRef(new Animated.Value(height * 0.25)).current;
  const kneeX  = useRef(new Animated.Value(width  * 0.5 )).current;
  const kneeY  = useRef(new Animated.Value(height * 0.52)).current;
  const ankleX = useRef(new Animated.Value(width  * 0.5 )).current;
  const ankleY = useRef(new Animated.Value(height * 0.78)).current;

  // Derived label origin — stays 14 px right and 4 px below the knee joint.
  const labelX = useRef(Animated.add(kneeX, 14)).current;
  const labelY = useRef(Animated.add(kneeY,  4)).current;

  // Pulse opacity — active only when uncertain (breathes 1.0 ↔ 0.35).
  const pulseAnim    = useRef(new Animated.Value(1)).current;
  const pulseLoopRef = useRef<Animated.CompositeAnimation | null>(null);

  useEffect(() => {
    pulseLoopRef.current?.stop();
    if (trackingStatus === "uncertain") {
      pulseLoopRef.current = Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 0.35, duration: 550, useNativeDriver: false }),
          Animated.timing(pulseAnim, { toValue: 1.00, duration: 550, useNativeDriver: false }),
        ])
      );
      pulseLoopRef.current.start();
    } else {
      pulseAnim.setValue(1);
    }
  }, [trackingStatus, pulseAnim]);

  // Keypoint spring animation.
  useEffect(() => {
    if (!keypoints) return;

    const anim = Animated.parallel([
      Animated.spring(hipX,   { toValue: keypoints.hip.x   * width,  ...SPRING }),
      Animated.spring(hipY,   { toValue: keypoints.hip.y   * height, ...SPRING }),
      Animated.spring(kneeX,  { toValue: keypoints.knee.x  * width,  ...SPRING }),
      Animated.spring(kneeY,  { toValue: keypoints.knee.y  * height, ...SPRING }),
      Animated.spring(ankleX, { toValue: keypoints.ankle.x * width,  ...SPRING }),
      Animated.spring(ankleY, { toValue: keypoints.ankle.y * height, ...SPRING }),
    ]);

    anim.start();
    return () => anim.stop();
  }, [keypoints, width, height]);

  const isLost      = trackingStatus === "lost";
  const boneOpacity = isLost ? 0.22 : 0.88;
  const boneWidth   = trackingStatus === "uncertain" ? 1.5 : 2.5;

  return (
    <Svg
      width={width}
      height={height}
      style={{ position: "absolute", top: 0, left: 0 }}
      pointerEvents="none"
    >
      {/* pulseAnim only modulates opacity when uncertain; stays at 1.0 otherwise */}
      <AnimatedG opacity={pulseAnim}>
        <G opacity={boneOpacity}>
          <AnimatedLine
            x1={hipX}   y1={hipY}
            x2={kneeX}  y2={kneeY}
            stroke={C_BONE} strokeWidth={boneWidth} strokeLinecap="round"
          />
          <AnimatedLine
            x1={kneeX}  y1={kneeY}
            x2={ankleX} y2={ankleY}
            stroke={C_BONE} strokeWidth={boneWidth} strokeLinecap="round"
          />
        </G>

        <AnimatedCircle
          cx={hipX} cy={hipY}
          r={7} fill="rgba(249,115,22,0.35)" stroke={C_HIP} strokeWidth={2}
        />
        <AnimatedCircle
          cx={kneeX} cy={kneeY}
          r={8} fill="rgba(14,165,233,0.35)" stroke={C_KNEE} strokeWidth={2}
        />
        <AnimatedCircle
          cx={ankleX} cy={ankleY}
          r={7} fill="rgba(34,197,94,0.35)" stroke={C_ANKLE} strokeWidth={2}
        />

        <AnimatedText
          x={labelX} y={labelY}
          fill={C_ANGLE} fontSize={13} fontFamily="monospace"
          opacity={isLost ? 0 : 0.95}
        >
          {`${Math.round(kneeAngleDeg)}°`}
        </AnimatedText>
      </AnimatedG>
    </Svg>
  );
});
