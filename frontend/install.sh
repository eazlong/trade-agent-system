docker stop qt
docker rm qt
docker rmi qt
docker build --tag qt .
docker run -dit -p3000:3000 --name qt qt