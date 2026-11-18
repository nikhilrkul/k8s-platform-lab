# syntax=docker/dockerfile:1
FROM golang:1.27 AS builder
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
# Static binary, no cgo, stripped: runs on a distroless image with no shell.
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/inventory ./cmd/inventory

# distroless/static has no shell or package manager, so it has very few CVEs.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /out/inventory /inventory
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/inventory"]
