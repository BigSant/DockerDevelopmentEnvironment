ARG ELASTICSEARCH_VERSION=""
FROM elasticsearch:${ELASTICSEARCH_VERSION} as base

#RUN sysctl -w vm.max_map_count=262144
#RUN echo "vm.max_map_count=262144" | tee /etc/sysctl.d/elasticsearch.conf

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
