# Этап сборки
FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src

# Копируем csproj и восстанавливаем зависимости
COPY ["bottg.csproj", "./"]
RUN dotnet restore "bottg.csproj"

# Копируем весь проект
COPY . .

# Публикуем и сразу копируем книги в publish
RUN dotnet publish "bottg.csproj" -c Release -o /app/publish /p:UseAppHost=false && \
    mkdir -p /app/publish/books && \
    cp -r /src/books/* /app/publish/books/ 2>/dev/null || true

# Финальный рантайм
FROM mcr.microsoft.com/dotnet/aspnet:9.0 AS final
WORKDIR /app

# Обязательные настройки для Render
ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080

# Копируем опубликованное приложение вместе с книгами
COPY --from=build /app/publish .

# Точка входа
ENTRYPOINT ["dotnet", "bottg.dll"]