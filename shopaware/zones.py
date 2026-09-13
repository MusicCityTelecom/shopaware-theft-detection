"""Typed polygons stored in normalized image coordinates."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Zone(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[\w-]+$')
    name: str = Field(min_length=1, max_length=128)
    type: Literal['merchandise', 'restricted', 'checkout', 'exit', 'ignore']
    points: list[tuple[float, float]] = Field(min_length=3, max_length=64)
    enabled: bool = True

    @model_validator(mode='after')
    def valid_polygon(self):
        if any(not math.isfinite(v) or not 0 <= v <= 1 for p in self.points for v in p):
            raise ValueError('Zone coordinates must be normalized to [0, 1]')
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(self.points, self.points[1:] + self.points[:1]))
        if abs(area) < 1e-6 or len(set(self.points)) != len(self.points):
            raise ValueError('Zone must have nonzero area and distinct vertices')
        # Reject intersections between nonadjacent polygon edges.
        def orientation(a, b, c):
            return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        edges = list(zip(self.points, self.points[1:] + self.points[:1]))
        for i, (a,b) in enumerate(edges):
            for j, (c,d) in enumerate(edges):
                if j <= i + 1 or (i == 0 and j == len(edges)-1):
                    continue
                if orientation(a,b,c)*orientation(a,b,d) <= 0 and orientation(c,d,a)*orientation(c,d,b) <= 0:
                    raise ValueError('Zone edges must not intersect')
        return self

    def contains(self, point: tuple[float, float]) -> bool:
        x, y = point
        inside = False
        for a, b in zip(self.points, self.points[1:] + self.points[:1]):
            if (a[1] > y) != (b[1] > y):
                crossing = (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]
                if x < crossing:
                    inside = not inside
        return self.enabled and inside
