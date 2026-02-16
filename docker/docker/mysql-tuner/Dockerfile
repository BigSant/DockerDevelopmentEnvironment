ARG DATABASE_USER=""
ARG DATABASE_PASSWORD=""

FROM alpine:3.20.3 as base

ARG DATABASE_USER=""
ARG DATABASE_PASSWORD=""
ARG MYSQLCLIENT_VERSION=10.11.8-r0
ARG PERL_VERSION=5.38.2-r0
ARG PERLDOC_VERSION=5.38.2-r0

RUN --mount=type=cache,target=/var/cache/apk \
    apk --update add \
        "mysql-client=${MYSQLCLIENT_VERSION}" \
        "perl=${PERL_VERSION}" \
        "perl-doc=${PERLDOC_VERSION}"

WORKDIR /
RUN wget http://mysqltuner.pl/ -O mysqltuner.pl
RUN wget https://raw.githubusercontent.com/major/MySQLTuner-perl/master/basic_passwords.txt -O basic_passwords.txt
RUN wget https://raw.githubusercontent.com/major/MySQLTuner-perl/master/vulnerabilities.csv -O vulnerabilities.csv

#ENTRYPOINT ["/usr/bin/perl", "/mysqltuner.pl", "--passwordfile", "/basic_passwords.txt",\
#             "--cvefile", "/vulnerabilities.txt", "--nosysstat", "--defaults-file", \
#             "/defaults.cnf", "--dumpdir", "/results", "--outputfile", \
#             "/results/mysqltuner.txt", "--reportfile", "/results/mysqltuner.html", \
#             "--host", "database", "--user", "sylius", "--pass=sylius"]
#ENTRYPOINT ["/usr/bin/perl", "/mysqltuner.pl", "--host=database", "--pass=sylius", "--user=sylius"]
ENTRYPOINT ["/usr/bin/perl", "/mysqltuner.pl", "--verbose", "--buffers", "--dbstat", "--idxstat", "--sysstat", "--pfstat", "--tbstat", "--host=database", "--user=root", "--pass=sylius"]
CMD ["--verbose" ]

#./mysqltuner.pl --user $MYSQL_USER --pass $MYSQL_PASSWORD


#RUN apt-get update && \
#    apt-get install -y \
#        perl=${PERL_VERSION} \
#        perl-doc=${PERLDOC_VERSION} \
#
#RUN rm -rf /var/lib/apt/lists/*

#CMD ["--host=database"]
#CMD ["--user=${DATABASE_USER}"]
#CMD ["--pass=${DATABASE_PASSWORD}"]
#CMD [ "/usr/bin/perl", "/mysqltuner.pl" , "--host=database"]
#ENTRYPOINT [ "/usr/bin/perl", "/mysqltuner.pl" , "--host=database"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
