# agentplay's viewer server as a long-running service. See "Run as a service
# (Docker)" in README.md, and compose.yaml for the mounts it expects.

FROM node:22-slim AS viewer
RUN corepack enable
WORKDIR /src
COPY scripts/collect_viewer_licenses.mjs scripts/
COPY viewer/package.json viewer/pnpm-lock.yaml viewer/
RUN cd viewer && corepack install && pnpm install --frozen-lockfile
COPY viewer viewer
RUN cd viewer && pnpm run build

FROM python:3.13-slim
COPY python /src/python
COPY --from=viewer /src/viewer/dist /src/python/src/agentplay/viewer
RUN pip install --no-cache-dir --root-user-action=ignore /src/python && rm -rf /src \
    && useradd --uid 1000 --create-home agentplay
ENV AGENTPLAY_HOME=/data \
    PYTHONUNBUFFERED=1
USER agentplay
EXPOSE 7317
ENTRYPOINT ["agentplay"]
CMD ["view", "--host", "0.0.0.0", "--no-open"]
