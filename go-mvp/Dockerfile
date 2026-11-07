FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /workshop .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 workshop
WORKDIR /app
COPY --from=build /workshop /app/workshop
USER 10001
EXPOSE 8080
ENTRYPOINT ["/app/workshop"]
CMD ["-migrate", "-seed", "-demo"]
