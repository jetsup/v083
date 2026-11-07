"""Fisheye dewarp: turns the raw circular image into a flat virtual-PTZ view.

Model: equidistant fisheye (r = R * theta / (lens_fov/2)). The camera is a
~180 degree lens; tune `lens_fov`, `cx`, `cy`, `radius` if the result looks off.
"""
import cv2
import numpy as np


class Dewarper:
    def __init__(self, enabled=False, yaw=0.0, pitch=50.0, fov=100.0, roll=0.0,
                 out_size=(960, 720), lens_fov=180.0,
                 cx=None, cy=None, radius=None):
        self.enabled = enabled
        self.yaw, self.pitch, self.fov, self.roll = yaw, pitch, fov, roll   # degrees
        self.out_w, self.out_h = out_size
        self.lens_fov = lens_fov
        self.cx, self.cy, self.radius = cx, cy, radius        # None = from frame size
        self._key = None
        self._maps = None

    # ---- controls -------------------------------------------------------
    def toggle(self):
        self.enabled = not self.enabled
        return self.enabled

    def pan(self, dyaw=0.0, dpitch=0.0, dzoom=0.0, droll=0.0):
        self.yaw = (self.yaw + dyaw + 180) % 360 - 180
        self.roll = (self.roll + droll + 180) % 360 - 180
        self.pitch = float(np.clip(self.pitch + dpitch, -89, 89))
        self.fov = float(np.clip(self.fov + dzoom, 30, 150))

    def reset(self):
        self.yaw, self.pitch, self.roll, self.fov = 0.0, 50.0, 0.0, 100.0

    # ---- processing -----------------------------------------------------
    def _build(self, w, h):
        cx = self.cx if self.cx is not None else w / 2
        cy = self.cy if self.cy is not None else h / 2
        R = self.radius if self.radius is not None else min(w, h) / 2

        f = (self.out_w / 2) / np.tan(np.radians(self.fov) / 2)
        u, v = np.meshgrid(np.arange(self.out_w), np.arange(self.out_h))
        rays = np.stack([(u - self.out_w / 2) / f,
                         (v - self.out_h / 2) / f,
                         np.ones_like(u, dtype=np.float64)], -1)

        yaw, pitch, roll = (np.radians(a) for a in (self.yaw, self.pitch, self.roll))
        rz = np.array([[np.cos(roll), -np.sin(roll), 0],
                       [np.sin(roll), np.cos(roll), 0],
                       [0, 0, 1]])
        rx = np.array([[1, 0, 0],
                       [0, np.cos(pitch), -np.sin(pitch)],
                       [0, np.sin(pitch), np.cos(pitch)]])
        ry = np.array([[np.cos(yaw), 0, np.sin(yaw)],
                       [0, 1, 0],
                       [-np.sin(yaw), 0, np.cos(yaw)]])
        rays = rays @ (rz @ ry @ rx).T
        rays /= np.linalg.norm(rays, axis=-1, keepdims=True)

        theta = np.arccos(np.clip(rays[..., 2], -1, 1))        # angle from optical axis
        phi = np.arctan2(rays[..., 1], rays[..., 0])
        r = R * theta / np.radians(self.lens_fov / 2)
        map_x = (cx + r * np.cos(phi)).astype(np.float32)
        map_y = (cy + r * np.sin(phi)).astype(np.float32)
        return map_x, map_y

    def apply(self, frame):
        if not self.enabled:
            return frame
        h, w = frame.shape[:2]
        key = (w, h, self.yaw, self.pitch, self.roll, self.fov, self.out_w, self.out_h,
               self.lens_fov, self.cx, self.cy, self.radius)
        if key != self._key:                                    # rebuild only on change
            self._maps, self._key = self._build(w, h), key
        return cv2.remap(frame, *self._maps, cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT)
