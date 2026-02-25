.PHONY: cp-up cp-down cp-logs cp-migrate cp-superuser cp-frontend-dev

cp-up:
	docker compose -f docker-compose.control-plane.yml up -d --build

cp-down:
	docker compose -f docker-compose.control-plane.yml down

cp-logs:
	docker compose -f docker-compose.control-plane.yml logs -f --tail=200

cp-migrate:
	cd apps/backend && python manage.py migrate

cp-superuser:
	cd apps/backend && python manage.py bootstrap_control_plane --username admin --email admin@example.com --password admin

cp-frontend-dev:
	cd apps/frontend && npm run dev
